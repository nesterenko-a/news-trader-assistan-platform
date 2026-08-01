from datetime import date, datetime, time, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.connection import get_session
from app.db.models import Security
from app.macro.service import event_tickers, list_events, list_security_events
from app.schemas import MacroEventOut

router = APIRouter(prefix="/macro", tags=["macro"])


def _to_day(d: date, end: bool = False) -> datetime:
    return datetime.combine(d, time.max if end else time.min, tzinfo=timezone.utc)


@router.get("/calendar", response_model=list[MacroEventOut])
async def macro_calendar(
    from_date: date | None = None,
    to_date: date | None = None,
    region: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    since = _to_day(from_date) if from_date else None
    until = _to_day(to_date, end=True) if to_date else None
    events = await list_events(session, since=since, until=until, region=region)
    result = []
    for event in events:
        item = _event_out(event)
        item["tickers"] = await event_tickers(session, event.id)
        result.append(item)
    return result


@router.get("/securities/{ticker}", response_model=list[MacroEventOut])
async def security_macro(
    ticker: str,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    security = await session.scalar(select(Security).where(Security.ticker == ticker.upper()))
    if security is None:
        raise HTTPException(status_code=404, detail="Бумага не найдена")
    result = []
    for event, tickers in await list_security_events(session, security.id):
        item = _event_out(event)
        item["tickers"] = tickers
        result.append(item)
    return result


def _event_out(event) -> dict:
    return {
        "id": event.id,
        "event_type": event.event_type,
        "title": event.title,
        "event_time": event.event_time,
        "region": event.region,
        "expected_impact": event.expected_impact,
        "market_wide": event.market_wide,
        "description": event.description,
        "tickers": [],
    }
