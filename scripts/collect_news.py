import argparse
import asyncio
import re
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select

from app.collectors.rss import DEFAULT_FEEDS, RSSCollector
from app.db.connection import SessionLocal, init_db
from app.db.models import Article, ArticleEntity, Entity, Source
from app.graph.service import resolve_entity_id
from app.llm.analyzer import ArticleAnalyzer
from app.llm.client import LLMClient

MAX_PER_FEED = 30


def _parse_since(args) -> datetime | None:
    if args.from_date:
        return datetime.combine(
            date.fromisoformat(args.from_date), time.min, tzinfo=timezone.utc
        )
    if args.days and args.days > 0:
        return datetime.now(timezone.utc) - timedelta(days=args.days)
    return None


def _within_since(published_at: datetime | None, since: datetime | None) -> bool:
    if since is None or published_at is None:
        return True
    return published_at >= since


async def _mention_check(
    session, text: str, entity_names: set[str] | None = None
) -> bool:
    entities = await session.scalars(select(Entity))
    lowered = text.lower()
    for entity in entities:
        if entity_names is not None and entity.name not in entity_names:
            continue
        for alias in [entity.name, *(entity.aliases or [])]:
            alias_str = str(alias).lower()
            if re.search(
                rf"(?<!\w){re.escape(alias_str)}(?!\w)", lowered
            ):
                return True
    return False


async def collect_news(
    session,
    since: datetime | None = None,
    entity_names: set[str] | None = None,
) -> int:
    client = LLMClient.from_settings()
    analyzer = ArticleAnalyzer(client)

    reputation = {}
    source_ids = {}
    for feed in DEFAULT_FEEDS:
        source = await session.scalar(select(Source).where(Source.name == feed["name"]))
        if source is None:
            source = Source(
                name=feed["name"],
                kind="rss",
                reputation_score=feed["reputation"],
                config={"url": feed["url"]},
            )
            session.add(source)
            await session.flush()
        source_ids[feed["name"]] = source.id
        reputation[feed["name"]] = source.reputation_score
    await session.commit()

    raw_articles = RSSCollector(DEFAULT_FEEDS).fetch()
    known_urls = set((await session.scalars(select(Article.url))).all())
    candidates = []
    seen_urls = set()
    for article in raw_articles:
        if article.url in seen_urls:
            continue
        seen_urls.add(article.url)
        if article.url in known_urls:
            continue
        if not _within_since(article.published_at, since):
            continue
        if await _mention_check(
            session, f"{article.title}\n{article.text}", entity_names
        ):
            candidates.append(article)
        if len(candidates) >= MAX_PER_FEED:
            break

    print(f"Collected {len(raw_articles)} raw, {len(candidates)} relevant to graph")

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
            analysis_version=f"llm:{client.model}",
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


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect, filter and analyze news via RSS + LLM"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=0,
        help="collect only items published in the last N days (0 = no limit)",
    )
    parser.add_argument(
        "--from",
        dest="from_date",
        default="",
        help="start date YYYY-MM-DD (overrides --days)",
    )
    parser.add_argument(
        "--entity",
        action="append",
        default=[],
        help="collect only items mentioning the given graph entity "
        "(repeatable; empty = all entities)",
    )
    args = parser.parse_args()

    since = _parse_since(args)
    entity_names = set(args.entity) or None
    if since is not None:
        print(f"Collecting news published since {since.isoformat()}")
    if entity_names:
        print(f"Target entities: {', '.join(sorted(entity_names))}")

    await init_db()
    async with SessionLocal() as session:
        stored = await collect_news(session, since=since, entity_names=entity_names)
        print(f"Stored {stored} analyzed articles")


if __name__ == "__main__":
    asyncio.run(main())
