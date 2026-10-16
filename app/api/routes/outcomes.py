from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db.connection import get_session
from app.db.models import ScenarioEvent, User
from app.tech_analysis.outcomes import list_outcomes, owned, summary

router = APIRouter(prefix="/top5/outcomes", tags=["top5-outcomes"])


class SubscriptionInput(BaseModel):
    channels: list[Literal["web", "telegram"]] = Field(default_factory=lambda: ["web"], min_length=1, max_length=2)
    kinds: list[Literal["zone", "confirmed", "open", "target", "stop", "cancelled", "expired_pending", "expired_open"]] = Field(default_factory=lambda: ["zone", "confirmed", "open", "target", "stop", "cancelled", "expired_pending", "expired_open"], min_length=1, max_length=8)


def check_origin(request):
    if request.headers.get("authorization", "").lower().startswith("bearer "):
        return
    from urllib.parse import urlsplit
    origin = urlsplit(request.headers.get("origin", ""))
    if origin.scheme not in ("http", "https") or origin.netloc != request.headers.get("host"):
        raise HTTPException(403, "Недопустимый источник запроса")


@router.get("")
async def outcome_list(ticker: str | None = None, state: str | None = None, model: str | None = None, since: datetime | None = None, until: datetime | None = None, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200), hide_finished: bool = False, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)) -> dict:
    return await list_outcomes(session, user.id, ticker, state, model, since, until, offset, limit, hide_finished)


@router.get("/summary")
async def outcome_summary(ticker: str | None = None, state: str | None = None, model: str | None = None, since: datetime | None = None, until: datetime | None = None, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)) -> dict:
    return await summary(session, user.id, ticker=ticker, state=state, model=model, since=since, until=until)


@router.get("/items/{snapshot_id}")
async def outcome_detail(snapshot_id: int, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)) -> dict:
    snapshot = await owned(session, snapshot_id, user.id)
    if snapshot is None:
        raise HTTPException(404, "Сценарий не найден")
    events = (await session.scalars(select(ScenarioEvent).where(ScenarioEvent.snapshot_id == snapshot_id).order_by(ScenarioEvent.id))).all()
    return {"id": snapshot.id, "raw": snapshot.raw, "plan": snapshot.plan,
            "events": [{"id": event.id, "kind": event.kind, "occurred_at": event.occurred_at, "recorded_at": event.recorded_at, "data": event.data} for event in events]}


@router.post("/items/{snapshot_id}/subscription")
async def subscribe(snapshot_id: int, body: SubscriptionInput, request: Request, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)) -> dict:
    check_origin(request)
    from app.tech_analysis.outcome_alerts import set_subscription
    return await set_subscription(session, user, snapshot_id, body.channels, body.kinds)


@router.delete("/items/{snapshot_id}/subscription")
async def unsubscribe(snapshot_id: int, request: Request, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)) -> dict:
    check_origin(request)
    from app.tech_analysis.outcome_alerts import set_subscription
    return await set_subscription(session, user, snapshot_id, [], [])
