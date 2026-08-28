"""REST API «Реальное время» (/v1): SSE-стрим live-котировок (docs/24).

GET /v1/realtime/stream?tickers=AFLT,SBER — Server-Sent Events:
  event: quote
  data: {"ticker":"AFLT","last":123.0,"open":...,"high":...,"low":...,"volume":...,"ts":"ISO-UTC"}

Эндпоинт требует аутентификации (как прочие приватные /v1). Агностичен к демону:
читает RealTimeQuote из БД. При отсутствии записи по тикеру — событие не шлётся.
"""

import asyncio
import json

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db.connection import SessionLocal, get_session
from app.db.models import (
    MarketOpenPosition,
    MarketOpenPositionClientGroup,
    RealTimeQuote,
    Security,
    User,
)

router = APIRouter(prefix="/realtime", tags=["realtime"])

POLL_INTERVAL = 5


async def _current_quotes(session: AsyncSession, tickers: list[str]) -> dict[str, RealTimeQuote]:
    """Текущие live-котировки по тикерам: {ticker: RealTimeQuote}."""
    if not tickers:
        return {}
    rows = (
        await session.execute(
            select(Security, RealTimeQuote)
            .join(RealTimeQuote, RealTimeQuote.security_id == Security.id)
            .where(Security.ticker.in_(tickers))
        )
    ).all()
    return {security.ticker: quote for security, quote in rows}


def _quote_event(security: Security, quote: RealTimeQuote) -> str:
    payload = {
        "ticker": security.ticker,
        "last": quote.last,
        "open": quote.open,
        "high": quote.high,
        "low": quote.low,
        "volume": quote.volume,
        "ts": quote.updated_at.isoformat() if quote.updated_at else None,
    }
    return f"event: quote\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _latest_oi(session: AsyncSession, security_id: int) -> dict | None:
    """Последнее значение OI фьючерса (open_position) + изменение к предыдущему дню
    + группы клиентов (физ/юр), из market_open_positions (обновляется демоном)."""
    rows = (
        await session.scalars(
            select(MarketOpenPosition)
            .where(MarketOpenPosition.security_id == security_id)
            .order_by(MarketOpenPosition.trading_date.desc())
            .limit(2)
        )
    ).all()
    if not rows:
        return None
    latest = rows[0]
    prev = rows[1] if len(rows) > 1 else None
    change_pct = None
    if prev is not None and prev.open_position:
        change_pct = round(
            (latest.open_position - prev.open_position) * 100.0 / prev.open_position, 2
        )
    groups = {"physical": None, "juridical": None}
    grows = (
        await session.scalars(
            select(MarketOpenPositionClientGroup).where(
                MarketOpenPositionClientGroup.security_id == security_id,
                MarketOpenPositionClientGroup.trading_date == latest.trading_date,
            )
        )
    ).all()
    for g in grows:
        groups[g.client_group] = {
            "long": g.long_pos,
            "short": g.short_pos,
            "net": g.long_pos - g.short_pos,
        }
    return {
        "ticker": None,  # заполняется в emit
        "date": latest.trading_date.isoformat() if latest.trading_date else None,
        "open_position": latest.open_position,
        "change_pct": change_pct,
        "groups": groups,
    }


def _oi_event(ticker: str, d: dict) -> str:
    payload = {
        "ticker": ticker,
        "date": d.get("date"),
        "open_position": d.get("open_position"),
        "change_pct": d.get("change_pct"),
        "groups": d.get("groups"),
    }
    return f"event: oi\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.get("/stream")
async def realtime_stream(
    tickers: str = Query("", description="Список тикеров через запятую"),
    _: User = Depends(get_current_user),
):
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]

    async def event_generator():
        # Переиспользуем отдельную сессию для долгоживущего стрима
        from app.db.connection import SessionLocal

        async with SessionLocal() as session:
            try:
                while True:
                    quotes = await _current_quotes(session, ticker_list)
                    for ticker in ticker_list:
                        security = security_by_ticker.get(ticker)
                        if security is None:
                            continue
                        quote = quotes.get(ticker)
                        if quote is not None:
                            yield _quote_event(security, quote)
                        # OI фьючерсов (если есть данные)
                        if security.security_type == "futures":
                            oi = await _latest_oi(session, security.id)
                            if oi is not None:
                                yield _oi_event(ticker, oi)
                    yield ": keep-alive\n\n"
                    await asyncio.sleep(POLL_INTERVAL)
            except asyncio.CancelledError:
                yield "event: end\ndata: byebye\n\n"
                raise

    # Загружаем Security один раз для отображения тикера (не меняются на лету)
    async def _load_securities():
        if not ticker_list:
            return {}
        async with SessionLocal() as session:
            rows = await session.scalars(select(Security).where(Security.ticker.in_(ticker_list)))
            return {s.ticker: s for s in rows.all()}

    security_by_ticker = await _load_securities()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
