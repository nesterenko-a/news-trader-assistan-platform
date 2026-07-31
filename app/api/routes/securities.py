from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.connection import get_session
from app.db.models import Article, ArticleEntity, Entity, Security
from app.graph.service import security_entity_ids
from app.schemas import NewsItemOut, SecuritySummary

router = APIRouter(prefix="/securities", tags=["securities"])


@router.get("/search", response_model=list[SecuritySummary])
async def search_securities(q: str, session: AsyncSession = Depends(get_session)) -> list[dict]:
    needle = q.strip().lower()
    rows = await session.scalars(select(Security).order_by(Security.ticker))
    matches = []
    for security in rows:
        haystack = " ".join(
            [security.ticker, security.name, *(security.aliases or [])]
        ).lower()
        if needle in haystack:
            matches.append(
                {
                    "ticker": security.ticker,
                    "name": security.name,
                    "market": security.market,
                    "security_type": security.security_type,
                    "sector": security.sector,
                    "currency": security.currency,
                }
            )
    return matches


@router.get("", response_model=list[SecuritySummary])
async def list_securities(session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = await session.scalars(select(Security).order_by(Security.ticker))
    return [
        {
            "ticker": s.ticker,
            "name": s.name,
            "market": s.market,
            "security_type": s.security_type,
            "sector": s.sector,
            "currency": s.currency,
        }
        for s in rows
    ]


@router.get("/{ticker}/news", response_model=list[NewsItemOut])
async def security_news(ticker: str, session: AsyncSession = Depends(get_session)) -> list[dict]:
    security = await session.scalar(select(Security).where(Security.ticker == ticker.upper()))
    if security is None:
        raise HTTPException(status_code=404, detail="Бумага не найдена")

    target_ids = await security_entity_ids(session, security.id)
    if not target_ids:
        return []

    entities = {e.id: e for e in await session.scalars(select(Entity))}
    article_entities = await session.scalars(
        select(ArticleEntity).where(ArticleEntity.entity_id.in_(target_ids))
    )
    article_ids = [ae.article_id for ae in article_entities]
    if not article_ids:
        return []

    articles = await session.scalars(
        select(Article).where(Article.id.in_(article_ids)).order_by(Article.published_at.desc())
    )
    source_names = {}
    from app.db.models import Source

    for src in await session.scalars(select(Source)):
        source_names[src.id] = src.name

    result = []
    for article in articles:
        mentions = [
            {"name": entities[ae.entity_id].name, "sentiment": ae.sentiment, "impact": ae.impact}
            for ae in article_entities
            if ae.article_id == article.id and ae.entity_id in entities
        ]
        result.append(
            {
                "id": article.id,
                "title": article.title,
                "url": article.url,
                "published_at": article.published_at,
                "source_name": source_names.get(article.source_id, ""),
                "summary": article.text[:300],
                "entities": mentions,
            }
        )
    return result
