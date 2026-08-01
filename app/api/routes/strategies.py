from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db.connection import get_session
from app.db.models import Security, Strategy, User
from app.schemas import StrategyHistoryItem

router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.get("/history", response_model=list[StrategyHistoryItem])
async def strategy_history(
    limit: int = 50,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    limit = max(1, min(limit, 200))
    rows = (
        await session.execute(
            select(Strategy, Security)
            .join(Security, Security.id == Strategy.security_id)
            .order_by(Strategy.generated_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "id": strategy.id,
            "ticker": security.ticker,
            "name": security.name,
            "verdict": strategy.verdict,
            "horizon": strategy.horizon,
            "confidence": strategy.confidence,
            "generated_at": strategy.generated_at,
            "model_version": strategy.model_version,
        }
        for strategy, security in rows
    ]
