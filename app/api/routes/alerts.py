from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.service import (
    get_settings,
    load_alerts,
    mark_all_read,
    mark_read,
    update_settings,
)
from app.auth import get_current_user
from app.db.connection import get_session
from app.db.models import Security, User
from app.schemas import AlertOut, AlertSettingsIn, AlertSettingsOut

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[AlertOut])
async def list_alerts(
    unread_only: bool = False,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    alerts = await load_alerts(session, user.id, unread_only=unread_only)
    tickers = {}
    if alerts:
        rows = await session.scalars(
            select(Security).where(Security.id.in_({a.security_id for a in alerts}))
        )
        tickers = {s.id: s.ticker for s in rows.all()}
    return [
        {
            "id": alert.id,
            "ticker": tickers.get(alert.security_id, ""),
            "headline": alert.headline,
            "url": alert.url,
            "impact": alert.impact,
            "is_ambiguous": alert.is_ambiguous,
            "is_read": alert.is_read,
            "created_at": alert.created_at,
        }
        for alert in alerts
    ]


@router.patch("/{alert_id}/read")
async def read_alert(
    alert_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if not await mark_read(session, user.id, alert_id):
        raise HTTPException(status_code=404, detail="Алерт не найден")
    return {"status": "ok"}


@router.post("/read-all")
async def read_all_alerts(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    count = await mark_all_read(session, user.id)
    return {"status": "ok", "marked": count}


@router.get("/settings", response_model=AlertSettingsOut)
async def alert_settings(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    settings = await get_settings(session, user.id)
    return {"min_impact": settings.min_impact, "channels": settings.channels or []}


@router.put("/settings", response_model=AlertSettingsOut)
async def alert_settings_update(
    payload: AlertSettingsIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    settings = await update_settings(
        session, user.id, min_impact=payload.min_impact, channels=payload.channels
    )
    return {"min_impact": settings.min_impact, "channels": settings.channels or []}
