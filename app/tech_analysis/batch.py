"""Сервис «Top-5: Теханализ группы акций из шаблона инструментов (kind=stock)».

Запускает по одному обычному анализу на каждую акцию шаблона (переиспользуя
актуальные успешные анализы), ведёт лёгкий ярлык-батч (tech_analysis_batches),
и формирует рейтинг Top-5 лучших сделок по Expected R.

Expected R каждого сценария = prob × rr − (1 − prob) × 1 (см. docs/25 §7).
Для каждой акции берётся «лучшая» стратегия — сценарий с наибольшим Expected R
среди A/B/C. Акции без валидных prob/rr исключаются из рейтинга.
"""

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import select

from app.config import get_settings
from app.db.models import FuturesTemplate, TechAnalysis, TechAnalysisBatch
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
) -> TechAnalysisBatch:
    """Запускает батч Теханализа по всем акциям шаблона kind=stock (async, фоном)."""
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
    asyncio.create_task(_run_batch(batch.id, tickers, user_id, provider))
    return batch


async def _run_batch(
    batch_id: int, tickers: list[str], user_id: int | None, provider: str | None
) -> None:
    """Фоновое выполнение батча: для каждой акции запускаем/переиспользуем анализ."""
    from app.db.connection import SessionLocal

    total = len(tickers)
    done = 0
    failed = 0
    try:
        async with SessionLocal() as session:
            for ticker in tickers:
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
    from sqlalchemy import func

    rows = await session.scalars(select(TechAnalysis).where(TechAnalysis.batch_id == batch_id))
    items = [r for r in rows.all()]
    total = len(items)
    success = sum(1 for r in items if r.status == "success")
    running = sum(1 for r in items if r.status == "running")
    failed = sum(1 for r in items if r.status == "failed")
    batch = await session.get(TechAnalysisBatch, batch_id)
    status = batch.status if batch else "unknown"
    # Светим реальный статус: если есть running — running/failed-неоконченные
    if running > 0 and status == "running":
        pass
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


def _scenario_metrics(sc: dict) -> dict:
    """Expected R + вторичные метрики сценария (R/R, вероятность, риск)."""
    prob = sc.get("probability")
    rr = sc.get("rr")
    er = expected_r(prob, rr)
    return {
        "title": sc.get("title") or "",
        "entry": sc.get("entry") or "",
        "stop": sc.get("stop") or "",
        "targets": sc.get("targets") or "",
        "why": sc.get("why") or "",
        "probability": prob,
        "rr": rr,
        "expected_r": round(er, 3) if er is not None else None,
    }


def best_strategy(tickers_analyses: list[dict]) -> dict | None:
    """Для одной акции выбирает сценарий с наибольшим Expected R (verdict BUY/SELL).

    Возвращает запись Top-5 вида {ticker, name, ...}, либо None, если нет
    валидного сценария.
    """
    best = None
    for ana in tickers_analyses:
        if not ana.get("scenario_json"):
            continue
        import json as _json

        try:
            data = _json.loads(ana["scenario_json"])
        except (ValueError, TypeError):
            continue
        cur_verdict = (ana.get("verdict") or "").upper()
        if cur_verdict not in ("BUY", "SELL"):
            continue
        for key in ("scenario_a", "scenario_b", "scenario_c"):
            sc = data.get(key) or {}
            er = expected_r(sc.get("probability"), sc.get("rr"))
            if er is None:
                continue
            candidate = {
                "ticker": ana.get("ticker"),
                "name": ana.get("name") or ana.get("ticker"),
                "strategy": key,
                "dir": "LONG" if cur_verdict == "BUY" else "SHORT",
                "entry": sc.get("entry") or "",
                "stop": sc.get("stop") or "",
                "targets": sc.get("targets") or "",
                "rr": sc.get("rr"),
                "probability": sc.get("probability"),
                "expected_r": round(er, 3),
                "analysis_id": ana.get("id"),
                "scenario": _scenario_metrics(sc),
            }
            if best is None or candidate["expected_r"] > best["expected_r"]:
                best = candidate
    return best


async def top5(session, template_id: int, limit: int = 5) -> dict:
    """Рейтинг Top-5 лучших сделок по акциям шаблона (по Expected R)."""
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
    results = []
    for ticker in tickers:
        ana = await _latest_analysis(session, ticker)
        if ana is None:
            continue
        item = best_strategy(
            [
                {
                    "ticker": ana.ticker,
                    "name": names.get(ana.ticker) or ana.ticker,
                    "id": ana.id,
                    "verdict": ana.verdict,
                    "scenario_json": ana.scenario_json,
                }
            ]
        )
        if item is not None:
            results.append(item)
    results.sort(key=lambda r: (r["expected_r"] is None, r["expected_r"] or 0), reverse=True)
    return {
        "template_id": template_id,
        "template": {"id": template.id, "name": template.name, "kind": template.kind},
        "total": len(results),
        "items": results[:limit],
    }
