"""Сервис «Теханализ в LLM»: запуск, этапы, повтор, нотисы.

Фоновый asyncio-таск по образцу ScriptRun (app/admin/runner.py): создаётся
запись TechAnalysis со статусом running, выполняется актуализация данных →
формирование запроса (request_md) → отправка в ChatGPT → парсинг ответа.
При сбое — статус failed + SystemNotice (source=tech_analysis_llm). Повтор
повторно отправляет тот же request_md (данные не пересобираются).
"""

import asyncio
from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from app.config import get_settings
from app.db.connection import SessionLocal
from app.db.models import MarketCandle, Security, TechAnalysis
from app.market.moex import MOEXClient
from app.market.oi_data import ensure_futures_security, sync_security_oi
from app.market.prices import sync_security_prices
from app.notices.service import add_notice
from app.tech_analysis.llm import ChatGPTClient, resolve_llm
from app.tech_analysis.parser import parse_response
from app.tech_analysis.request_builder import build_analysis_request

SOURCE_NOTICE = "tech_analysis_llm"


def friendly_llm_error(exc: Exception) -> str:
    """Человеко-понятное описание ошибки LLM для карточки/нотиса.

    429 (RateLimitError) — превышен лимит запросов/квоты OpenAI: объясняем и
    предлагаем повторить позже. 401 (AuthenticationError) — неверный ключ.
    """
    try:
        import openai

        if isinstance(exc, openai.RateLimitError):
            return (
                "Превышен лимит запросов к LLM (HTTP 429) — временное ограничение. "
                "Повторите попытку позже."
            )
        if isinstance(exc, openai.AuthenticationError):
            return "Неверный API-ключ LLM (HTTP 401). Проверьте CHATGPT_API_KEY / LLM_API_KEY."
    except ImportError:
        pass
    text = str(exc)
    if "429" in text or "Too Many Requests" in text or "RateLimit" in text:
        return "Превышен лимит запросов к LLM (HTTP 429) — временное ограничение. Повторите позже."
    if "401" in text or "AuthenticationError" in text or "Incorrect API key" in text:
        return "Неверный API-ключ LLM (HTTP 401). Проверьте ключ в настройках."
    return text[:1000]

# Единовременно активная запись на тикер: {ticker: analysis_id}
_active: dict[str, int] = {}


async def _load_security(session, ticker: str) -> Security | None:
    """Находит бумагу по тикеру; если это фьючерс и его нет в справочнике —
    создаёт на лету из списка фьючерсов MOEX (on-demand, без предварительного update_oi)."""
    ticker = ticker.upper()
    security = await session.scalar(select(Security).where(Security.ticker == ticker))
    if security is not None:
        return security
    # Пробуем создать фьючерс на лету по данным MOEX (engines/futures/markets/forts)
    try:
        futures = await MOEXClient().fetch_futures_list()
    except Exception:  # noqa: BLE001
        return None
    for f in futures:
        if (f.get("secid") or "").upper() == ticker:
            return await ensure_futures_security(
                session,
                ticker,
                shortname=f.get("shortname") or ticker,
                assetcode=f.get("assetcode"),
                lastdeldate=f.get("lastdeldate"),
            )
    return None


async def _mark_stale_and_fail(session, analysis_id: int) -> None:
    row = await session.get(TechAnalysis, analysis_id)
    if row is None:
        return
    row.status = "failed"
    row.stage = "done"
    row.error = row.error or "Анализ прерван (перезапуск приложения)"
    row.finished_at = datetime.utcnow()
    await session.commit()


async def mark_stale_running() -> int:
    """При старте приложения помечает все активные (running) записи как failed."""
    async with SessionLocal() as session:
        rows = (
            await session.scalars(select(TechAnalysis).where(TechAnalysis.status == "running"))
        ).all()
        for row in rows:
            await _mark_stale_and_fail(session, row.id)
        return len(rows)


def has_active(ticker: str) -> bool:
    return ticker.upper() in _active


async def start_analysis(
    session,
    ticker: str,
    user_id: int | None = None,
    provider: str | None = None,
    batch_id: int | None = None,
) -> TechAnalysis:
    """Создаёт запись и запускает фоновый анализ. Возвращает запись."""
    ticker = ticker.upper()
    security = await _load_security(session, ticker)
    if security is None:
        raise ValueError("Бумага не найдена")
    if ticker in _active:
        raise RuntimeError("Анализ по этому тикеру уже выполняется")

    provider = (provider or "").strip().lower() or None
    resolved = resolve_llm(provider)
    if not resolved.api_key:
        raise RuntimeError(
            "Не задан ключ LLM (CHATGPT_API_KEY или LLM_API_KEY) в настройках "
            "(.env) — тех. анализ в LLM недоступен"
        )

    analysis = TechAnalysis(
        user_id=user_id,
        ticker=ticker,
        is_future=(security.security_type == "futures"),
        status="running",
        stage="refreshing_data",
        model=resolved.model,
        provider=provider or ("chatgpt" if "openai.com" in resolved.base_url else "deepseek"),
        batch_id=batch_id,
    )
    session.add(analysis)
    await session.commit()
    await session.refresh(analysis)

    _active[ticker] = analysis.id
    asyncio.create_task(_analyze(analysis.id, ticker))
    return analysis


async def _analyze(analysis_id: int, ticker: str) -> None:
    """Полный конвейер анализа на отдельной сессии."""
    try:
        async with SessionLocal() as session:
            row = await session.get(TechAnalysis, analysis_id)
            if row is None:
                return
            # Этап 1: актуализация данных
            row.stage = "refreshing_data"
            await session.commit()
            security = await _load_security(session, ticker)
            if security is None:
                raise RuntimeError("Бумага не найдена")
            is_future = security.security_type == "futures"
            await sync_security_prices(session, ticker, days=7)
            if is_future:
                # Цена базового актива (спота) — для корректного базиса «фьючерс vs спот»
                spot_ticker = (security.assetcode or "").strip().upper()
                try:
                    if spot_ticker:
                        await sync_security_prices(session, spot_ticker, days=7)
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"[tech_analysis] {ticker}: спот {spot_ticker} не обновлён "
                        f"({type(exc).__name__}: {exc})"
                    )
                # OI — устойчиво: сбой OI не валит весь анализ
                try:
                    await sync_security_oi(session, ticker, days=7)
                except Exception as exc:  # noqa: BLE001
                    print(
                        f"[tech_analysis] {ticker}: OI не обновлён ({type(exc).__name__}: {exc})"
                    )

            # Проверка актуальности котировок: для анализа нужна свежая цена
            last_candle = await session.scalar(
                select(func.max(MarketCandle.trading_date)).where(
                    MarketCandle.security_id == security.id
                )
            )
            stale_days = 45
            if last_candle is None:
                raise RuntimeError(
                    f"Недостаточно данных по бумаге {ticker}: в базе нет котировок. "
                    "Обновите цены (scripts.update_prices) или уточните тикер."
                )
            if last_candle < date.today() - timedelta(days=stale_days):
                raise RuntimeError(
                    f"Недостаточно данных по бумаге {ticker}: последняя котировка "
                    f"от {last_candle.isoformat()} (старше {stale_days} дней). "
                    "Обновите тикер или ценовой источник."
                )

            # Этап 2: формирование запроса
            row.stage = "forming_request"
            await session.commit()
            built = await build_analysis_request(session, security)
            row.request_md = built["request_md"]
            row.is_future = built["is_future"]
            row.price_at_analysis = built.get("price_at_analysis")
            await session.commit()

            # Этап 3: отправка в LLM (этот момент — кнопка «Скачать запрос» уже активна)
            row.stage = "awaiting_llm"
            await session.commit()
            resolved = resolve_llm(row.provider)
            client = ChatGPTClient(
                base_url=resolved.base_url, api_key=resolved.api_key,
                model=resolved.model, timeout=resolved.timeout,
            )
            response = await client.chat(
                system_prompt="Ты — опытный технический аналитик.",
                user_prompt=row.request_md,
            )

            row.response_md = response
            parsed = parse_response(response)
            row.verdict = parsed["verdict"] or None
            row.entry = parsed["entry"] or None
            row.tp = parsed["tp"] or None
            row.sl = parsed["sl"] or None
            row.scenario_json = parsed["scenario_json"]
            row.status = "success"
            row.stage = "done"
            row.finished_at = datetime.utcnow()
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        try:
            async with SessionLocal() as session:
                row = await session.get(TechAnalysis, analysis_id)
                if row is not None:
                    row.status = "failed"
                    row.stage = "done"
                    row.error = friendly_llm_error(exc)
                    row.finished_at = datetime.utcnow()
                    await session.commit()
                    await add_notice(
                        session,
                        "critical",
                        f"Теханализ «{ticker}» завершился с ошибкой: {friendly_llm_error(exc)[:200]}",
                        source=SOURCE_NOTICE,
                    )
        finally:
            _active.pop(ticker, None)
    else:
        _active.pop(ticker, None)


async def retry_analysis(analysis_id: int) -> TechAnalysis:
    """Повтор отправки того же request_md (для failed-записи)."""
    async with SessionLocal() as session:
        row = await session.get(TechAnalysis, analysis_id)
        if row is None:
            raise KeyError("Анализ не найден")
        ticker = row.ticker.upper()
        if ticker in _active:
            raise RuntimeError("Анализ по этому тикеру уже выполняется")
        settings = get_settings()
        if not row.request_md:
            raise RuntimeError("Запрос не сформирован — повторить нельзя")
        row.status = "running"
        row.stage = "awaiting_llm"
        row.error = None
        row.finished_at = None
        await session.commit()
        _active[ticker] = row.id
    asyncio.create_task(_analyze_retry(analysis_id, ticker))
    return row


async def _analyze_retry(analysis_id: int, ticker: str) -> None:
    """Повтор: только отправка в LLM с сохранённым request_md."""
    try:
        async with SessionLocal() as session:
            row = await session.get(TechAnalysis, analysis_id)
            if row is None or not row.request_md:
                raise RuntimeError("Запрос не сформирован")
            resolved = resolve_llm(row.provider)
            client = ChatGPTClient(
                base_url=resolved.base_url, api_key=resolved.api_key,
                model=resolved.model, timeout=resolved.timeout,
            )
            response = await client.chat(
                system_prompt="Ты — опытный технический аналитик.",
                user_prompt=row.request_md,
            )
            row.response_md = response
            parsed = parse_response(response)
            row.verdict = parsed["verdict"] or None
            row.entry = parsed["entry"] or None
            row.tp = parsed["tp"] or None
            row.sl = parsed["sl"] or None
            row.scenario_json = parsed["scenario_json"]
            row.status = "success"
            row.stage = "done"
            row.finished_at = datetime.utcnow()
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        try:
            async with SessionLocal() as session:
                row = await session.get(TechAnalysis, analysis_id)
                if row is not None:
                    row.status = "failed"
                    row.stage = "done"
                    row.error = friendly_llm_error(exc)
                    row.finished_at = datetime.utcnow()
                    await session.commit()
                    await add_notice(
                        session,
                        "critical",
                        f"Теханализ «{ticker}» завершился с ошибкой: {friendly_llm_error(exc)[:200]}",
                        source=SOURCE_NOTICE,
                    )
        finally:
            _active.pop(ticker, None)
    else:
        _active.pop(ticker, None)


async def list_analyses(ticker: str, page: int = 1, per_page: int = 4) -> dict:
    """Страница карточек анализов по тикеру (≤4 на страницу)."""
    async with SessionLocal() as session:
        base = select(TechAnalysis).where(
            TechAnalysis.ticker == ticker.upper(), TechAnalysis.status != ""
        )
        total = await session.scalar(
            select(func.count()).select_from(
                select(TechAnalysis)
                .where(TechAnalysis.ticker == ticker.upper())
                .subquery()
            )
        )
        rows = (
            await session.scalars(
                base.order_by(TechAnalysis.id.desc())
                .offset((page - 1) * per_page)
                .limit(per_page)
            )
        ).all()
        items = []
        for r in rows:
            items.append(
                {
                    "id": r.id,
                    "ticker": r.ticker,
                    "status": r.status,
                    "stage": r.stage,
                    "verdict": r.verdict,
                    "entry": r.entry,
                    "tp": r.tp,
                    "sl": r.sl,
                    "model": r.model,
                    "provider": r.provider,
                    "created_at": r.created_at.strftime("%d.%m.%Y %H:%M")
                    if r.created_at
                    else "",
                    "finished_at": r.finished_at.strftime("%d.%m.%Y %H:%M")
                    if r.finished_at
                    else "",
                    "error": r.error,
                }
            )
        return {
            "items": items,
            "total": total or 0,
            "page": page,
            "per_page": per_page,
            "pages": max(1, (total + per_page - 1) // per_page) if total else 1,
        }
