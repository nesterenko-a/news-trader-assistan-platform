"""Сервис «Top-5: Теханализ группы акций из шаблона инструментов (kind=stock)».

Запускает по одному обычному анализу на каждую акцию шаблона (переиспользуя
актуальные успешные анализы), ведёт лёгкий ярлык-батч (tech_analysis_batches),
и формирует рейтинг Top-5 лучших сделок по Expected R.

Expected R каждого сценария = prob × rr − (1 − prob) × 1 (см. docs/25 §7).
Для каждой акции берётся «лучшая» стратегия — сценарий с наибольшим Expected R
среди A/B/C. Акции без валидных prob/rr исключаются из рейтинга.
"""

import asyncio
import json as _json
from datetime import datetime, timedelta

from sqlalchemy import select

from app.config import get_settings
from app.db.models import FuturesTemplate, MarketCandle, RealTimeQuote, TechAnalysis, TechAnalysisBatch
from app.tech_analysis.parser import expected_r
from app.tech_analysis.service import start_analysis

# Порог свежести анализа на акцию (если анализ успешен и не старше — переиспользуем)
FRESH_HOURS = get_settings().top5_fresh_hours


def _ticker_list(template: FuturesTemplate) -> list[str]:
    return [
        t.strip().upper()
        for t in (template.tickers or "").replace(";", ",").split(",")
        if t.strip()
    ]


async def get_stock_template(session, template_id: int) -> FuturesTemplate | None:
    template = await session.get(FuturesTemplate, template_id)
    if template is None or template.kind != "stock":
        return None
    return template


async def fresh_analysis(session, ticker: str) -> TechAnalysis | None:
    """Возвращает переиспользуемый успешный анализ по тикеру (свежий)."""
    since = datetime.utcnow() - timedelta(hours=FRESH_HOURS)
    row = await session.scalar(
        select(TechAnalysis)
        .where(
            TechAnalysis.ticker == ticker.upper(),
            TechAnalysis.status == "success",
            TechAnalysis.finished_at >= since,
        )
        .order_by(TechAnalysis.id.desc())
        .limit(1)
    )
    return row


async def create_batch(session, template_id: int, user_id: int | None) -> TechAnalysisBatch:
    batch = TechAnalysisBatch(user_id=user_id, template_id=template_id, status="running")
    session.add(batch)
    await session.commit()
    await session.refresh(batch)
    return batch


async def start_batch(
    session,
    template_id: int,
    user_id: int | None = None,
    provider: str | None = None,
    force: bool = False,
) -> TechAnalysisBatch:
    """Запускает батч Теханализа по всем акциям шаблона kind=stock (async, фоном).

    force=True — игнорировать свежесть (TOP5_FRESH_HOURS) и перезапускать
    Теханализ по всем акциям шаблона (без переиспользования актуальных анализов).
    """
    template = await get_stock_template(session, template_id)
    if template is None:
        raise ValueError("Шаблон не найден или не является шаблоном акций (kind != stock)")

    tickers = _ticker_list(template)[: get_settings().top5_max_instruments]
    if not tickers:
        raise ValueError("Шаблон пуст")

    # Единовременный активный батч на шаблон
    active = await session.scalar(
        select(TechAnalysisBatch).where(
            TechAnalysisBatch.template_id == template_id,
            TechAnalysisBatch.status == "running",
        )
    )
    if active is not None:
        raise RuntimeError("По этому шаблону уже выполняется батч Теханализа")

    batch = await create_batch(session, template_id, user_id)
    asyncio.create_task(_run_batch(batch.id, tickers, user_id, provider, force))
    return batch


async def _run_batch(
    batch_id: int, tickers: list[str], user_id: int | None, provider: str | None,
    force: bool = False,
) -> None:
    """Фоновое выполнение батча: запускаем/переиспользуем анализ по каждой акции."""
    from app.db.connection import SessionLocal

    total = len(tickers)
    done = 0
    failed = 0
    try:
        async with SessionLocal() as session:
            for ticker in tickers:
                if not force:
                    existing = await fresh_analysis(session, ticker)
                    if existing is not None:
                        # Переиспользуем актуальный успешный анализ (не гоняем LLM).
                        existing.batch_id = batch_id
                        done += 1
                        await session.commit()
                        continue
                try:
                    await start_analysis(
                        session, ticker, user_id=user_id, provider=provider, batch_id=batch_id
                    )
                    done += 1
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    print(f"[top5] {ticker}: не удалось запустить ({type(exc).__name__}: {exc})")
            # Статус батча считаем мгновенно: если все запущены/переиспользованы — success,
            # иначе partial (при сбоях запуска). Реальное завершение LLM пересчитывается
            # при просмотре Top-5 (batch_progress).
            await _finalize_batch(session, batch_id, total, done, failed)
    except Exception as exc:  # noqa: BLE001
        try:
            async with SessionLocal() as session:
                await _finalize_batch(session, batch_id, total, done, failed, error=str(exc))
        except Exception:
            pass


async def _finalize_batch(session, batch_id: int, total: int, done: int, failed: int, error: str | None = None) -> None:
    batch = await session.get(TechAnalysisBatch, batch_id)
    if batch is None:
        return
    if failed == total:
        batch.status = "failed"
    elif failed > 0:
        batch.status = "partial"
    else:
        batch.status = "success"
    batch.finished_at = datetime.utcnow()
    if error:
        # при серьёзном сбое не теряем прогресс
        batch.status = "partial"
    await session.commit()


async def batch_progress(session, batch_id: int) -> dict:
    """Текущий прогресс батча по анализам (для поллинга /top5)."""
    rows = await session.scalars(select(TechAnalysis).where(TechAnalysis.batch_id == batch_id))
    items = [r for r in rows.all()]
    total = len(items)
    success = sum(1 for r in items if r.status == "success")
    running = sum(1 for r in items if r.status == "running")
    failed = sum(1 for r in items if r.status == "failed")
    # Живой статус по анализам: пока есть running — идёт выполнение; иначе успех/частичный/сбой.
    if running > 0:
        status = "running"
    elif success == total and total > 0:
        status = "success"
    elif failed > 0:
        status = "failed" if success == 0 else "partial"
    else:
        status = "running"
    return {
        "batch_id": batch_id,
        "status": status,
        "total": total,
        "done": success + failed,
        "success": success,
        "running": running,
        "failed": failed,
        "stage": "running" if running else ("success" if status == "success" else "has-failed"),
    }


async def list_batches(session, template_id: int, limit: int = 10) -> list[dict]:
    """История батчей Top-5 по шаблону (последние N) с анализами и статусами."""
    batches = (
        await session.scalars(
            select(TechAnalysisBatch)
            .where(TechAnalysisBatch.template_id == template_id)
            .order_by(TechAnalysisBatch.id.desc())
            .limit(limit)
        )
    ).all()
    result = []
    for b in batches:
        analyses = (
            await session.scalars(
                select(TechAnalysis)
                .where(TechAnalysis.batch_id == b.id)
                .order_by(TechAnalysis.ticker)
            )
        ).all()
        items = [
            {
                "ticker": a.ticker,
                "status": a.status,
                "stage": a.stage,
                "verdict": a.verdict,
                "error": a.error,
                "analysis_id": a.id,
                "scenario_json": a.scenario_json,
                "await_confirmation": a.await_confirmation,
                "finished_at": a.finished_at,
            }
            for a in analyses
        ]
        batch_tickers = [i["ticker"] for i in items]
        prices = await _current_prices(session, batch_tickers)
        # Собственный Top-5 батча: по его успешным анализам (сценарии + вердикт)
        top_rows = _rank_best_rows(
            [
                {
                    "ticker": a["ticker"],
                    "name": a["ticker"],
                    "id": a["analysis_id"],
                    "verdict": a["verdict"],
                    "scenario_json": a["scenario_json"],
                    "await_confirmation": a.get("await_confirmation"),
                    "price": prices.get(a["ticker"]),
                    "as_of": a.get("finished_at"),
                }
                for a in items
                if a["status"] == "success" and a.get("scenario_json")
            ]
        )
        # Дата формирования прогноза батча
        batch_as_of = None
        for r in top_rows:
            if r.get("as_of") is not None:
                ts = r["as_of"].timestamp()
                if batch_as_of is None or ts > batch_as_of.timestamp():
                    batch_as_of = r["as_of"]
        success = sum(1 for a in analyses if a.status == "success")
        running = sum(1 for a in analyses if a.status == "running")
        failed_n = sum(1 for a in analyses if a.status == "failed")
        if running > 0:
            live_status = "running"
        elif success == len(items) and items:
            live_status = "success"
        elif failed_n > 0:
            live_status = "failed" if success == 0 else "partial"
        else:
            live_status = b.status
        result.append(
            {
                "id": b.id,
                "status": live_status,
                "created_at": b.created_at.strftime("%d.%m.%Y %H:%M") if b.created_at else "",
                "finished_at": b.finished_at.strftime("%d.%m.%Y %H:%M") if b.finished_at else "",
                "as_of": batch_as_of,
                "total": len(items),
                "success": success,
                "running": running,
                "failed": failed_n,
                "items": items,
                "top5": top_rows,
            }
        )
    return result


async def _latest_analysis(session, ticker: str) -> TechAnalysis | None:
    since = datetime.utcnow() - timedelta(hours=FRESH_HOURS)
    return await session.scalar(
        select(TechAnalysis)
        .where(
            TechAnalysis.ticker == ticker.upper(),
            TechAnalysis.status == "success",
            TechAnalysis.finished_at >= since,
        )
        .order_by(TechAnalysis.id.desc())
        .limit(1)
    )


def _first_num(text: str) -> float | None:
    """Первое число из строки (для диапazonов/списков)."""
    if not text:
        return None
    clean = str(text).replace(",", ".").replace(" ", "")
    import re as _re

    m = _re.search(r"-?\d+(?:\.\d+)?", clean)
    if not m:
        return None
    return float(m.group())


def _entry_mid(text: str) -> float | None:
    """Средняя точка диапазона входа: '100-105' → 102.5, '31.0–31.3' → 31.15."""
    if not text:
        return None
    import re as _re

    nums = [float(x) for x in _re.findall(r"\d+\.?\d*", str(text))]
    if not nums:
        return None
    return sum(nums) / len(nums)


def _first_target(text: str) -> float | None:
    """Первая цель из списка '110/115' → 110."""
    return _first_num(text)


def compute_rr(sc: dict, fallback: bool = True) -> float | None:
    """R/R сценария: из явного rr LLM или fallback из уровней entry/stop/targets."""
    rr = sc.get("rr")
    if rr is not None:
        return float(rr)
    if not fallback:
        return None
    stop = _first_num(sc.get("stop"))
    entry = _entry_mid(sc.get("entry"))
    target = _first_num(sc.get("targets"))
    risk = None
    reward = None
    if stop is not None and entry is not None:
        risk = abs(entry - stop)
    if target is not None and entry is not None:
        reward = abs(target - entry)
    if reward is not None and risk and risk > 0:
        return round(reward / risk, 3)
    return None


def compute_risk_pct(sc: dict) -> float | None:
    """Риск до стопа (%). None, если нет входа/стопа."""
    stop = _first_num(sc.get("stop"))
    entry = _entry_mid(sc.get("entry"))
    if stop is None or entry is None or entry == 0:
        return None
    return round(abs(entry - stop) * 100.0 / abs(entry), 3)


def _quality_score(sc: dict) -> float:
    """Качество подтверждения/структуры (0..1) — наличие уровней и обоснования."""
    parts = 0
    if sc.get("entry"):
        parts += 1
    if sc.get("stop"):
        parts += 1
    if sc.get("targets"):
        parts += 1
    if sc.get("why"):
        parts += 1
    if sc.get("probability") is not None or sc.get("rr") is not None:
        parts += 1
    return parts / 5.0


def compute_score(sc: dict) -> float | None:
    """Композитный Score (как в макете): 35% уверенность · 30% R/R · 20% риск · 15% качество."""
    prob = sc.get("probability")
    rr = compute_rr(sc)
    risk = compute_risk_pct(sc)
    if rr is None or risk is None:
        return None
    # Уверенность сценария (0..100)
    conf = 0.0 if prob is None else min(prob * 100.0, 100.0)
    # R/R: за верх 3.0 → 100, ниже → пропорционально
    rr_score = min(rr / 3.0, 1.0) * 100.0
    # Риск: ≤4% → 100, выше → падает до 0 на ~20%
    risk_score = max(0.0, 1.0 - max(0.0, risk - 4.0) / 16.0) * 100.0
    qual = _quality_score(sc) * 100.0
    score = 0.35 * conf + 0.30 * rr_score + 0.20 * risk_score + 0.15 * qual
    return round(score, 1)


def _scenario_metrics(sc: dict) -> dict:
    """Expected R + вторичные метрики сценария (R/R, вероятность, риск, Score)."""
    prob = sc.get("probability")
    rr = compute_rr(sc)
    er = expected_r(prob, rr)
    return {
        "title": sc.get("title") or "",
        "entry": sc.get("entry") or "",
        "stop": sc.get("stop") or "",
        "targets": sc.get("targets") or "",
        "why": sc.get("why") or "",
        "probability": prob,
        "rr": rr,
        "risk": compute_risk_pct(sc),
        "score": compute_score(sc),
        "expected_r": round(er, 3) if er is not None else None,
    }


def _all_scenarios(ana: dict) -> dict:
    """Все три сценария A/B/C анализа как {a, b, c} с метриками (для раскрытия строки Top-5)."""
    out = {"a": {}, "b": {}, "c": {}}
    if not ana.get("scenario_json"):
        return out
    try:
        data = _json.loads(ana["scenario_json"])
    except (ValueError, TypeError):
        return out
    for letter, key in (("a", "scenario_a"), ("b", "scenario_b"), ("c", "scenario_c")):
        sc = data.get(key) or {}
        out[letter] = _scenario_metrics(sc) if isinstance(sc, dict) else {}
    return out


async def _current_prices(session, tickers: list[str]) -> dict:
    """Текущая цена по тикерам: live-котировка (RealTimeQuote.last) или последний close свечи."""
    from app.db.models import Security

    if not tickers:
        return {}
    secs = (
        await session.scalars(select(Security).where(Security.ticker.in_(tickers)))
    ).all()
    sec_by_ticker = {s.ticker: s.id for s in secs}
    idx_by_id = {sid: ticker for ticker, sid in sec_by_ticker.items()}
    ids = list(idx_by_id.keys())
    prices: dict[str, float] = {}
    if ids:
        quotes = await session.scalars(
            select(RealTimeQuote).where(RealTimeQuote.security_id.in_(ids))
        )
        for q in quotes.all():
            ticker = idx_by_id.get(q.security_id)
            if ticker and q.last is not None:
                prices[ticker] = float(q.last)
    # Fallback: последний close дневной свечи для тикеров без live-котировки
    for sid in ids:
        ticker = idx_by_id[sid]
        if ticker in prices:
            continue
        candle = await session.scalar(
            select(MarketCandle)
            .where(MarketCandle.security_id == sid)
            .order_by(MarketCandle.trading_date.desc())
            .limit(1)
        )
        if candle and candle.close is not None:
            prices[ticker] = float(candle.close)
    return prices




def best_strategy(tickers_analyses: list[dict]) -> dict | None:
    """Для одной акции выбирает сценарий с наибольшим Expected R.

    Учитываются вердикты BUY/SELL/WAIT (чтобы результат был виден, даже если
    система по всем акциям сказала «выжидать» или сделки «неинтересны»).
    Возвращает запись Top-5 вида {ticker, name, dir, ..., scenarios}, либо None,
    если нет валидного сценария (не парсится JSON или нет prob/rr).
    """
    best = None
    for ana in tickers_analyses:
        if not ana.get("scenario_json"):
            continue
        try:
            data = _json.loads(ana["scenario_json"])
        except (ValueError, TypeError):
            continue
        cur_verdict = (ana.get("verdict") or "").upper()
        if cur_verdict not in ("BUY", "SELL", "WAIT"):
            continue
        for key in ("scenario_a", "scenario_b", "scenario_c"):
            sc = data.get(key) or {}
            er = expected_r(sc.get("probability"), compute_rr(sc))
            if er is None:
                continue
            dir_label = {"BUY": "LONG", "SELL": "SHORT", "WAIT": "WAIT"}.get(cur_verdict, "WAIT")
            candidate = {
                "ticker": ana.get("ticker"),
                "name": ana.get("name") or ana.get("ticker"),
                "strategy": key,
                "dir": dir_label,
                "verdict": cur_verdict,
                "await_confirmation": bool(ana.get("await_confirmation")),
                "entry": sc.get("entry") or "",
                "stop": sc.get("stop") or "",
                "targets": sc.get("targets") or "",
                "rr": compute_rr(sc),
                "probability": sc.get("probability"),
                "risk": compute_risk_pct(sc),
                "score": compute_score(sc),
                "expected_r": round(er, 3),
                "analysis_id": ana.get("id"),
                "scenario": _scenario_metrics(sc),
                # Все три сценария для раскрытия строки (идентично «Полному разбору»)
                "scenarios": _all_scenarios(ana),
                "price": ana.get("price"),
                "as_of": ana.get("as_of"),
            }
            if best is None or candidate["expected_r"] > best["expected_r"]:
                best = candidate
    return best


def _rank_best_rows(rows: list[dict]) -> list[dict]:
    """Сворачивает список анализов (группируя по тикеру) в ранжированный Top-N.

    rows — список словарей {ticker, name, id, verdict, scenario_json}.
    Возвращает список «лучших стратегий» по акциям, отсортированный по Expected R.
    """
    by_ticker: dict[str, list[dict]] = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append(r)
    results = []
    for ticker, anas in by_ticker.items():
        item = best_strategy(anas)
        if item is not None:
            results.append(item)
    results.sort(key=lambda r: (r["expected_r"] is None, r["expected_r"] or 0), reverse=True)
    return results


async def top5(session, template_id: int, limit: int = 5) -> dict:
    """Рейтинг Top-5 лучших сделок по акциям шаблона (по Expected R; включает WAIT)."""
    template = await get_stock_template(session, template_id)
    if template is None:
        raise ValueError("Шаблон не найден или не является шаблоном акций")
    tickers = _ticker_list(template)
    from app.db.models import Security

    names = {}
    if tickers:
        secs = await session.scalars(
            select(Security).where(Security.ticker.in_(tickers))
        )
        names = {s.ticker: s.name for s in secs.all()}
    prices = await _current_prices(session, tickers)
    rows = []
    for ticker in tickers:
        ana = await _latest_analysis(session, ticker)
        if ana is None:
            continue
        rows.append(
            {
                "ticker": ana.ticker,
                "name": names.get(ana.ticker) or ana.ticker,
                "id": ana.id,
                "verdict": ana.verdict,
                "scenario_json": ana.scenario_json,
                "await_confirmation": ana.await_confirmation,
                "price": prices.get(ana.ticker),
                "as_of": ana.finished_at,
            }
        )
    results = _rank_best_rows(rows)
    # Дата формирования прогноза: максимум finished_at по анализам, вошедшим в рейтинг
    as_of = None
    for r in results:
        if r.get("as_of") is not None:
            ts = r["as_of"].timestamp()
            if as_of is None or ts > as_of.timestamp():
                as_of = r["as_of"]
    # Агрегаты для панели метрик (как в макете)
    qualified = [r for r in results if _deal_qualified(r)]
    best_score_row = max(results, key=lambda r: r.get("score") or 0) if results and any(
        r.get("score") is not None for r in results
    ) else None
    return {
        "template_id": template_id,
        "template": {"id": template.id, "name": template.name, "kind": template.kind},
        "as_of": as_of,
        "total": len(results),
        "qualified": len(qualified),
        "best_score": best_score_row["score"] if best_score_row else None,
        "items": results[:limit],
    }


def _deal_qualified(r: dict) -> bool:
    """Фильтр качества сделки (как в макете): R/R ≥ 1.5, риск ≤ 4%, уверенность ≥ 40%."""
    rr = r.get("rr")
    risk = r.get("risk")
    prob = r.get("probability")
    if rr is None or risk is None:
        return False
    if rr < 1.5:
        return False
    if risk > 4.0:
        return False
    if prob is not None and prob < 0.40:
        return False
    return True
