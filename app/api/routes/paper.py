from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db.connection import get_session
from app.db.models import Security, User
from app.paper.service import (
    account_view,
    close_position,
    get_or_create_account,
    reset_account,
)
from app.schemas import PaperOut, PaperTradeOut

router = APIRouter(prefix="/paper", tags=["paper"])


@router.get("", response_model=PaperOut)
async def paper_portfolio(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    account = await get_or_create_account(session, user.id)
    return await account_view(session, account)


@router.get("/trades", response_model=list[PaperTradeOut])
async def paper_trades(
    limit: int = 100,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    account = await get_or_create_account(session, user.id)
    view = await account_view(session, account)
    return view["trades"][: max(1, min(limit, 500))]


@router.delete("/{ticker}")
async def paper_close_position(
    ticker: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    account = await get_or_create_account(session, user.id)
    security = await session.scalar(select(Security).where(Security.ticker == ticker.upper()))
    if security is None:
        raise HTTPException(status_code=404, detail="Бумага не найдена")
    from app.paper.service import latest_closes

    closes = await latest_closes(session, [security.id])
    price = closes.get(security.id)
    if price is None:
        raise HTTPException(status_code=400, detail="Нет цены для закрытия позиции")
    if not await close_position(session, account, security.id, price[1]):
        raise HTTPException(status_code=404, detail="Открытой позиции нет")
    return {"status": "ok"}


@router.post("/reset")
async def paper_reset(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    account = await get_or_create_account(session, user.id)
    await reset_account(session, account)
    return {"status": "ok"}
