from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Alert,
    AlertSettings,
    Article,
    ArticleEntity,
    User,
    WatchlistItem,
)
from app.graph.service import security_entity_ids

DEFAULT_MIN_IMPACT = 0.7
DEFAULT_CHANNELS = ["app"]
ALERT_LOOKBACK_DAYS = 7


async def get_settings(session: AsyncSession, user_id: int) -> AlertSettings:
    settings = await session.get(AlertSettings, user_id)
    if settings is None:
        settings = AlertSettings(
            user_id=user_id, min_impact=DEFAULT_MIN_IMPACT, channels=list(DEFAULT_CHANNELS)
        )
        session.add(settings)
        await session.commit()
    return settings


async def update_settings(
    session: AsyncSession,
    user_id: int,
    min_impact: float | None = None,
    channels: list[str] | None = None,
) -> AlertSettings:
    settings = await get_settings(session, user_id)
    if min_impact is not None:
        settings.min_impact = max(0.0, min(1.0, min_impact))
    if channels is not None:
        valid = {c for c in channels if c in {"app", "telegram"}}
        settings.channels = sorted(valid) if valid else list(DEFAULT_CHANNELS)
    await session.commit()
    return settings


async def process_alerts(session: AsyncSession, since: datetime | None = None) -> list[Alert]:
    users = (await session.scalars(select(User))).all()
    created: list[Alert] = []
    for user in users:
        settings = await get_settings(session, user.id)
        watch_items = (
            await session.scalars(
                select(WatchlistItem).where(WatchlistItem.user_id == user.id)
            )
        ).all()
        for item in watch_items:
            entity_ids = await security_entity_ids(session, item.security_id)
            if not entity_ids:
                continue
            conditions = [
                ArticleEntity.entity_id.in_(entity_ids),
                ArticleEntity.impact >= settings.min_impact,
            ]
            if since is not None:
                conditions.append(Article.published_at >= since)
            rows = (
                await session.execute(
                    select(ArticleEntity, Article)
                    .join(Article, Article.id == ArticleEntity.article_id)
                    .where(*conditions)
                    .order_by(Article.published_at.desc())
                )
            ).all()
            for ae, article in rows:
                existing = await session.scalar(
                    select(Alert).where(
                        Alert.user_id == user.id,
                        Alert.article_id == article.id,
                        Alert.security_id == item.security_id,
                    )
                )
                if existing is not None:
                    continue
                alert = Alert(
                    user_id=user.id,
                    security_id=item.security_id,
                    article_id=article.id,
                    headline=article.title,
                    url=article.url,
                    impact=ae.impact,
                    is_ambiguous=(ae.sentiment == "neutral"),
                )
                session.add(alert)
                created.append(alert)
    await session.commit()
    return created


async def load_alerts(
    session: AsyncSession, user_id: int, unread_only: bool = False
) -> list[Alert]:
    statement = select(Alert).where(Alert.user_id == user_id)
    if unread_only:
        statement = statement.where(Alert.is_read == False)  # noqa: E712
    statement = statement.order_by(Alert.created_at.desc())
    return list((await session.scalars(statement)).all())


async def mark_read(session: AsyncSession, user_id: int, alert_id: int) -> bool:
    alert = await session.scalar(
        select(Alert).where(Alert.id == alert_id, Alert.user_id == user_id)
    )
    if alert is None:
        return False
    alert.is_read = True
    await session.commit()
    return True


async def mark_all_read(session: AsyncSession, user_id: int) -> int:
    alerts = await load_alerts(session, user_id, unread_only=True)
    for alert in alerts:
        alert.is_read = True
    await session.commit()
    return len(alerts)
