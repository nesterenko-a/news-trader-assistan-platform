from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Article, ArticleEntity, Entity, Source
from app.graph.service import security_entity_ids
from app.presentation.view import NewsItemView


async def load_security_news(
    session: AsyncSession,
    security_id: int,
    limit: int = 30,
) -> list[NewsItemView]:
    target_ids = await security_entity_ids(session, security_id)
    if not target_ids:
        return []

    entities = {e.id: e.name for e in await session.scalars(select(Entity))}
    mentions = (
        await session.scalars(
            select(ArticleEntity).where(ArticleEntity.entity_id.in_(target_ids))
        )
    ).all()
    article_ids = [m.article_id for m in mentions]
    if not article_ids:
        return []

    articles = (
        await session.scalars(
            select(Article)
            .where(Article.id.in_(article_ids))
            .order_by(Article.published_at.desc())
            .limit(limit)
        )
    ).all()
    sources = {s.id: s.name for s in await session.scalars(select(Source))}

    items = []
    for article in articles:
        tags = [
            (entities[m.entity_id], m.sentiment)
            for m in mentions
            if m.article_id == article.id and m.entity_id in entities
        ]
        items.append(
            NewsItemView(
                title=article.title,
                url=article.url,
                source_name=sources.get(article.source_id, ""),
                date_str=article.published_at.strftime("%d.%m.%Y %H:%M")
                if article.published_at
                else "",
                entity_tags=tags,
            )
        )
    return items
