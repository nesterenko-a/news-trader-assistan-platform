from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db.connection import get_session
from app.db.models import Security, User, UserFavorite

router = APIRouter(prefix="/favorites", tags=["favorites"])


async def _security(session: AsyncSession, ticker: str) -> Security:
    security = await session.scalar(select(Security).where(Security.ticker == ticker.upper()))
    if security is None:
        raise HTTPException(status_code=404, detail="Бумага не найдена")
    if security.security_type not in {"stock", "futures"}:
        raise HTTPException(status_code=400, detail="Тип бумаги не поддерживается")
    return security


@router.get("")
async def list_favorites(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    rows = (await session.execute(select(UserFavorite, Security).join(Security).where(UserFavorite.user_id == user.id).order_by(UserFavorite.created_at.desc()))).all()
    return {"items": [{"ticker": s.ticker, "name": s.name, "security_type": s.security_type, "sector": s.sector, "market": s.market, "created_at": f.created_at.isoformat() if f.created_at else None} for f, s in rows]}


@router.put("/{ticker}")
async def add_favorite(ticker: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    security = await _security(session, ticker)
    existing = await session.scalar(select(UserFavorite).where(UserFavorite.user_id == user.id, UserFavorite.security_id == security.id))
    if existing is None:
        session.add(UserFavorite(user_id=user.id, security_id=security.id))
        await session.commit()
    return {"ticker": security.ticker, "is_favorite": True}


@router.delete("/{ticker}")
async def remove_favorite(ticker: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    security = await _security(session, ticker)
    await session.execute(delete(UserFavorite).where(UserFavorite.user_id == user.id, UserFavorite.security_id == security.id))
    await session.commit()
    return {"ticker": security.ticker, "is_favorite": False}
