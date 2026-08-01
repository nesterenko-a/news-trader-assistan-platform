from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Strategy, UserFeedback

VALID_RATINGS = {"worked", "partial", "neutral", "failed"}
RATING_LABELS = {
    "worked": "Сработало",
    "partial": "Частично",
    "neutral": "Нейтрально",
    "failed": "Не сработало",
}


async def set_feedback(
    session: AsyncSession,
    strategy_id: int,
    user_id: int,
    rating: str,
    comment: str = "",
) -> UserFeedback:
    if rating not in VALID_RATINGS:
        raise ValueError(f"Недопустимая оценка: {rating}")
    strategy = await session.get(Strategy, strategy_id)
    if strategy is None:
        raise KeyError(f"Стратегия {strategy_id} не найдена")
    feedback = await session.scalar(
        select(UserFeedback).where(
            UserFeedback.strategy_id == strategy_id,
            UserFeedback.user_id == user_id,
        )
    )
    if feedback is None:
        feedback = UserFeedback(
            strategy_id=strategy_id, user_id=user_id, rating=rating, comment=comment
        )
        session.add(feedback)
    else:
        feedback.rating = rating
        feedback.comment = comment
    await session.commit()
    return feedback


async def get_rating(
    session: AsyncSession, strategy_id: int, user_id: int
) -> str | None:
    feedback = await session.scalar(
        select(UserFeedback).where(
            UserFeedback.strategy_id == strategy_id,
            UserFeedback.user_id == user_id,
        )
    )
    return feedback.rating if feedback else None


async def ratings_map(
    session: AsyncSession, strategy_ids: list[int], user_id: int
) -> dict[int, str]:
    if not strategy_ids:
        return {}
    rows = (
        await session.scalars(
            select(UserFeedback).where(
                UserFeedback.user_id == user_id,
                UserFeedback.strategy_id.in_(strategy_ids),
            )
        )
    ).all()
    return {f.strategy_id: f.rating for f in rows}


async def user_stats(session: AsyncSession, user_id: int) -> dict:
    rows = (
        await session.scalars(
            select(UserFeedback).where(UserFeedback.user_id == user_id)
        )
    ).all()
    counts = {"worked": 0, "partial": 0, "neutral": 0, "failed": 0}
    for f in rows:
        if f.rating in counts:
            counts[f.rating] += 1
    total = len(rows)
    worked_percent = round(counts["worked"] / total * 100, 1) if total else None
    return {**counts, "total": total, "worked_percent": worked_percent}


async def record_feedback_for_security(
    session: AsyncSession, security_id: int, user_id: int, rating: str
) -> bool:
    strategy = await session.scalar(
        select(Strategy)
        .where(Strategy.security_id == security_id)
        .order_by(Strategy.generated_at.desc(), Strategy.id.desc())
        .limit(1)
    )
    if strategy is None:
        return False
    await set_feedback(session, strategy.id, user_id, rating)
    return True
