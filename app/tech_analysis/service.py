"""Сервис «Теханализ в LLM»: запуск, этапы, повтор, нотисы.

Фоновый asyncio-таск по образцу ScriptRun (app/admin/runner.py): создаётся
запись TechAnalysis со статусом running, выполняется актуализация данных →
формирование запроса (request_md) → отправка в ChatGPT → парсинг ответа.
При сбое — статус failed + SystemNotice (source=tech_analysis_llm). Повтор
повторно отправляет тот же request_md (данные не пересобираются).
"""

import asyncio
from datetime import datetime

from sqlalchemy import func, select

from app.config import get_settings
from app.db.connection import SessionLocal
from app.db.models import Security, TechAnalysis
from app.market.oi_data import sync_security_oi
from app.market.prices import sync_security_prices
from app.notices.service import add_notice
from app.tech_analysis.llm import ChatGPTClient
from app.tech_analysis.parser import parse_response
from app.tech_analysis.request_builder import build_analysis_request

SOURCE_NOTICE = "tech_analysis_llm"

# Единовременно активная запись на тикер: {ticker: analysis_id}
_active: dict[str, int] = {}


async def _load_security(session, ticker: str) -> Security | None:
    return await session.scalar(select(Security).where(Security.ticker == ticker.upper()))


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


async def start_analysis(session, ticker: str, user_id: int | None = None) -> TechAnalysis:
    """Создаёт запись и запускает фоновый анализ. Возвращает запись."""
    ticker = ticker.upper()
    security = await _load_security(session, ticker)
    if security is None:
        raise ValueError("Бумага не найдена")
    if ticker in _active:
        raise RuntimeError("Анализ по этому тикеру уже выполняется")

    settings = get_settings()
    if not settings.chatgpt_api_key:
        raise RuntimeError(
            "Не задан CHATGPT_API_KEY в настройках (.env) — тех. анализ в LLM недоступен"
        )

    analysis = TechAnalysis(
        user_id=user_id,
        ticker=ticker,
        is_future=(security.security_type == "futures"),
        status="running",
        stage="refreshing_data",
        model=settings.chatgpt_model,
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
                await sync_security_oi(session, ticker, days=7)

            # Этап 2: формирование запроса
            row.stage = "forming_request"
            await session.commit()
            built = await build_analysis_request(session, security)
            row.request_md = built["request_md"]
            row.is_future = built["is_future"]
            await session.commit()

            # Этап 3: отправка в LLM (этот момент — кнопка «Скачать запрос» уже активна)
            row.stage = "awaiting_llm"
            await session.commit()
            client = ChatGPTClient.from_settings()
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
                    row.error = str(exc)[:1000]
                    row.finished_at = datetime.utcnow()
                    await session.commit()
                    await add_notice(
                        session,
                        "critical",
                        f"Теханализ «{ticker}» завершился с ошибкой: {str(exc)[:200]}",
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
            client = ChatGPTClient.from_settings()
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
                    row.error = str(exc)[:1000]
                    row.finished_at = datetime.utcnow()
                    await session.commit()
                    await add_notice(
                        session,
                        "critical",
                        f"Теханализ «{ticker}» завершился с ошибкой: {str(exc)[:200]}",
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
