from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MacroEvent, Security, macro_event_security

REGION_WIDE = {"RU", "US", "global"}


async def list_events(
    session: AsyncSession,
    since: datetime | None = None,
    until: datetime | None = None,
    region: str | None = None,
) -> list[MacroEvent]:
    statement = select(MacroEvent).order_by(MacroEvent.event_time)
    if since is not None:
        statement = statement.where(MacroEvent.event_time >= since)
    if until is not None:
        statement = statement.where(MacroEvent.event_time <= until)
    if region:
        statement = statement.where(MacroEvent.region == region)
    return list((await session.scalars(statement)).all())


async def event_tickers(session: AsyncSession, event_id: int) -> list[str]:
    rows = (
        await session.execute(
            select(Security.ticker)
            .join(macro_event_security, macro_event_security.c.security_id == Security.id)
            .where(macro_event_security.c.event_id == event_id)
            .order_by(Security.ticker)
        )
    ).all()
    return [r[0] for r in rows]


async def list_security_events(
    session: AsyncSession, security_id: int, limit: int = 10
) -> list[tuple[MacroEvent, list[str]]]:
    bound_rows = (
        await session.scalars(
            select(MacroEvent)
            .join(macro_event_security, macro_event_security.c.event_id == MacroEvent.id)
            .where(macro_event_security.c.security_id == security_id)
        )
    ).all()
    bound_ids = {e.id for e in bound_rows}
    wide_rows = (
        await session.scalars(
            select(MacroEvent)
            .where(MacroEvent.market_wide.is_(True), MacroEvent.region.in_(REGION_WIDE))
            .order_by(MacroEvent.event_time)
        )
    ).all()
    events = sorted(
        {e.id: e for e in [*bound_rows, *wide_rows]}.values(),
        key=lambda e: e.event_time,
    )
    result = []
    for event in events[-limit:]:
        tickers = await event_tickers(session, event.id)
        result.append((event, tickers))
    return result
