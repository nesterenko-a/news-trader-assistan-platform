from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db.connection import get_session
from app.db.models import PortfolioPosition, Security, Strategy, User
from app.market.moex import MOEXClient
from app.schemas import PositionIn, PositionOut, PositionUpdateIn

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

_moex = MOEXClient()


async def _latest_verdict(session: AsyncSession, security_id: int) -> str | None:
    strategy = await session.scalar(
        select(Strategy)
        .where(Strategy.security_id == security_id)
        .order_by(Strategy.generated_at.desc())
        .limit(1)
    )
    return strategy.verdict if strategy else None


def _position_out(security: Security, position: PortfolioPosition, price: float | None) -> dict:
    cost_basis = round(position.quantity * position.avg_price, 2)
    market_value = round(position.quantity * price, 2) if price else None
    pnl = round(market_value - cost_basis, 2) if market_value is not None else None
    pnl_percent = (
        round((market_value / cost_basis - 1) * 100, 2)
        if market_value is not None and cost_basis
        else None
    )
    return {
        "ticker": security.ticker,
        "name": security.name,
        "sector": security.sector,
        "quantity": position.quantity,
        "avg_price": position.avg_price,
        "current_price": price,
        "market_value": market_value,
        "cost_basis": cost_basis,
        "pnl": pnl,
        "pnl_percent": pnl_percent,
        "verdict": None,
    }


@router.get("", response_model=list[PositionOut])
async def list_positions(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    rows = (
        await session.execute(
            select(PortfolioPosition, Security)
            .join(Security, Security.id == PortfolioPosition.security_id)
            .where(PortfolioPosition.user_id == user.id)
            .order_by(PortfolioPosition.opened_at)
        )
    ).all()
    result = []
    for position, security in rows:
        quote = await _moex.fetch_quote(security.ticker)
        item = _position_out(security, position, quote["price"] if quote else None)
        item["verdict"] = await _latest_verdict(session, security.id)
        result.append(item)
    return result


@router.post("", response_model=PositionOut, status_code=status.HTTP_201_CREATED)
async def add_position(
    payload: PositionIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if payload.quantity <= 0:
        raise HTTPException(status_code=400, detail="Количество должно быть положительным")
    if payload.avg_price <= 0:
        raise HTTPException(status_code=400, detail="Цена входа должна быть положительной")
    security = await session.scalar(
        select(Security).where(Security.ticker == payload.ticker.strip().upper())
    )
    if security is None:
        raise HTTPException(status_code=404, detail="Бумага не найдена")
    existing = await session.scalar(
        select(PortfolioPosition).where(
            PortfolioPosition.user_id == user.id, PortfolioPosition.security_id == security.id
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="Позиция по бумаге уже есть")
    position = PortfolioPosition(
        user_id=user.id,
        security_id=security.id,
        quantity=payload.quantity,
        avg_price=payload.avg_price,
    )
    session.add(position)
    await session.commit()
    return _position_out(security, position, payload.avg_price)


@router.patch("/{ticker}", response_model=PositionOut)
async def update_position(
    ticker: str,
    payload: PositionUpdateIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    security = await session.scalar(select(Security).where(Security.ticker == ticker.upper()))
    if security is None:
        raise HTTPException(status_code=404, detail="Бумага не найдена")
    position = await session.scalar(
        select(PortfolioPosition).where(
            PortfolioPosition.user_id == user.id, PortfolioPosition.security_id == security.id
        )
    )
    if position is None:
        raise HTTPException(status_code=404, detail="Позиция не найдена")
    if payload.quantity is not None:
        if payload.quantity <= 0:
            raise HTTPException(status_code=400, detail="Количество должно быть положительным")
        position.quantity = payload.quantity
    if payload.avg_price is not None:
        if payload.avg_price <= 0:
            raise HTTPException(status_code=400, detail="Цена входа должна быть положительной")
        position.avg_price = payload.avg_price
    await session.commit()
    quote = await _moex.fetch_quote(security.ticker)
    item = _position_out(security, position, quote["price"] if quote else None)
    item["verdict"] = await _latest_verdict(session, security.id)
    return item


@router.delete("/{ticker}")
async def remove_position(
    ticker: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    security = await session.scalar(select(Security).where(Security.ticker == ticker.upper()))
    if security is None:
        raise HTTPException(status_code=404, detail="Бумага не найдена")
    await session.execute(
        delete(PortfolioPosition).where(
            PortfolioPosition.user_id == user.id, PortfolioPosition.security_id == security.id
        )
    )
    await session.commit()
    return {"status": "ok"}
