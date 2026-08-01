import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.rss import RawArticle
from app.db.models import Article, ArticleEntity, Entity, Source
from app.graph.service import resolve_entity_id


def within_since(published_at: datetime | None, since: datetime | None) -> bool:
    if since is None or published_at is None:
        return True
    return published_at >= since


async def mention_check(
    session: AsyncSession, text: str, entity_names: set[str] | None = None
) -> bool:
    entities = await session.scalars(select(Entity))
    lowered = text.lower()
    for entity in entities:
        if entity_names is not None and entity.name not in entity_names:
            continue
        for alias in [entity.name, *(entity.aliases or [])]:
            alias_str = str(alias).lower()
            if re.search(rf"(?<!\w){re.escape(alias_str)}(?!\w)", lowered):
                return True
    return False


async def ensure_sources(
    session: AsyncSession, sources: list[dict]
) -> tuple[dict[str, int], dict[str, float]]:
    source_ids: dict[str, int] = {}
    reputation: dict[str, float] = {}
    for item in sources:
        name = item["name"]
        source = await session.scalar(select(Source).where(Source.name == name))
        if source is None:
            source = Source(
                name=name,
                kind=item["kind"],
                reputation_score=item.get("reputation", 0.5),
                config=item.get("config", {}),
            )
            session.add(source)
            await session.flush()
        source_ids[name] = source.id
        reputation[name] = source.reputation_score
    await session.commit()
    return source_ids, reputation


async def filter_candidates(
    session: AsyncSession,
    raw_articles: list[RawArticle],
    since: datetime | None,
    entity_names: set[str] | None,
    known_urls: set[str],
    max_candidates: int = 30,
) -> list[RawArticle]:
    candidates: list[RawArticle] = []
    seen_urls: set[str] = set()
    for article in raw_articles:
        if article.url in seen_urls:
            continue
        seen_urls.add(article.url)
        if article.url in known_urls:
            continue
        if not within_since(article.published_at, since):
            continue
        if await mention_check(
            session, f"{article.title}\n{article.text}", entity_names
        ):
            candidates.append(article)
        if len(candidates) >= max_candidates:
            break
    return candidates


async def ingest_candidates(
    session: AsyncSession,
    candidates: list[RawArticle],
    source_ids: dict[str, int],
    reputation: dict[str, float],
    analyzer,
    model_version: str,
) -> int:
    stored = 0
    for article in candidates:
        exists = await session.scalar(select(Article).where(Article.url == article.url))
        if exists is not None:
            continue

        analysis = await analyzer.analyze(article.title, article.text)
        if not analysis.is_reliable:
            continue

        record = Article(
            title=article.title,
            text=article.text,
            url=article.url,
            source_id=source_ids.get(article.source_name),
            source_reputation=reputation.get(article.source_name, 0.5),
            published_at=article.published_at,
            language="ru",
            analysis_version=model_version,
        )
        session.add(record)
        await session.flush()

        for ent in analysis.entities:
            entity_id = await resolve_entity_id(session, ent.name)
            if entity_id is None:
                continue
            session.add(
                ArticleEntity(
                    article_id=record.id,
                    entity_id=entity_id,
                    sentiment=ent.sentiment,
                    impact=ent.impact,
                    snippet=ent.snippet,
                    entity_role=ent.role,
                    topic=analysis.topic,
                )
            )
        stored += 1

    await session.commit()
    return stored
