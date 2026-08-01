from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db.connection import get_session
from app.db.models import Security, Strategy, User, WatchlistItem
from app.schemas import WatchlistAddIn, WatchlistItemOut

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


async def _latest_verdict(session: AsyncSession, security_id: int) -> tuple[str, str] | None:
    strategy = await session.scalar(
        select(Strategy)
        .where(Strategy.security_id == security_id)
        .order_by(Strategy.generated_at.desc())
        .limit(1)
    )
    if strategy is None:
        return None
    return strategy.verdict, strategy.confidence


@router.get("", response_model=list[WatchlistItemOut])
async def list_watchlist(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    rows = (
        await session.execute(
            select(WatchlistItem, Security)
            .join(Security, Security.id == WatchlistItem.security_id)
            .where(WatchlistItem.user_id == user.id)
            .order_by(WatchlistItem.created_at)
        )
    ).all()
    result = []
    for item, security in rows:
        verdict = await _latest_verdict(session, security.id)
        result.append(
            {
                "ticker": security.ticker,
                "name": security.name,
                "sector": security.sector,
                "market": security.market,
                "verdict": verdict[0] if verdict else None,
                "confidence": verdict[1] if verdict else None,
            }
        )
    return result


@router.post("", response_model=WatchlistItemOut, status_code=status.HTTP_201_CREATED)
async def add_to_watchlist(
    payload: WatchlistAddIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    security = await session.scalar(
        select(Security).where(Security.ticker == payload.ticker.strip().upper())
    )
    if security is None:
        raise HTTPException(status_code=404, detail="Бумага не найдена")
    existing = await session.scalar(
        select(WatchlistItem).where(
            WatchlistItem.user_id == user.id, WatchlistItem.security_id == security.id
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="Бумага уже в watchlist")
    session.add(WatchlistItem(user_id=user.id, security_id=security.id))
    await session.commit()
    return {
        "ticker": security.ticker,
        "name": security.name,
        "sector": security.sector,
        "market": security.market,
    }


@router.delete("/{ticker}")
async def remove_from_watchlist(
    ticker: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    security = await session.scalar(select(Security).where(Security.ticker == ticker.upper()))
    if security is None:
        raise HTTPException(status_code=404, detail="Бумага не найдена")
    await session.execute(
        delete(WatchlistItem).where(
            WatchlistItem.user_id == user.id, WatchlistItem.security_id == security.id
        )
    )
    await session.commit()
    return {"status": "ok"}
