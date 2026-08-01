from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db.connection import get_session
from app.db.models import Security, Strategy, User
from app.feedback.service import get_rating, ratings_map, set_feedback, user_stats
from app.schemas import (
    FeedbackIn,
    FeedbackOut,
    FeedbackStats,
    StrategyHistoryItem,
)

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
    strategy_ids = [strategy.id for strategy, _ in rows]
    ratings = await ratings_map(session, strategy_ids, user.id)
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
            "my_rating": ratings.get(strategy.id),
        }
        for strategy, security in rows
    ]


@router.post("/{strategy_id}/feedback", response_model=FeedbackOut)
async def strategy_feedback(
    strategy_id: int,
    payload: FeedbackIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        feedback = await set_feedback(
            session, strategy_id, user.id, payload.rating, payload.comment
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Стратегия не найдена")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "id": feedback.id,
        "strategy_id": feedback.strategy_id,
        "rating": feedback.rating,
        "comment": feedback.comment,
        "created_at": feedback.created_at,
    }


@router.get("/feedback/stats", response_model=FeedbackStats)
async def feedback_stats(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await user_stats(session, user.id)
