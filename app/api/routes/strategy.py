from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.connection import get_session
from app.schemas import StrategyResponse
from app.strategy.engine import generate_strategy

router = APIRouter(prefix="/securities", tags=["strategy"])


@router.post("/{ticker}/strategy", response_model=StrategyResponse)
async def get_strategy(
    ticker: str, session: AsyncSession = Depends(get_session)
) -> dict:
    try:
        return await generate_strategy(session, ticker.upper())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
