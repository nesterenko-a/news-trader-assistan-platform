import argparse
import asyncio
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select

from app.collectors.rss import DEFAULT_FEEDS, fetch_rss_feeds
from app.collectors.telegram import TelegramCollector
from app.config import get_settings
from app.db.connection import SessionLocal, init_db
from app.db.models import Article
from app.llm.analyzer import ArticleAnalyzer
from app.llm.client import LLMClient
from app.news.ingest import ensure_sources, filter_candidates, ingest_candidates
from app.news.sources_service import get_rss_feeds

MAX_PER_FEED = 30


def _parse_since(args) -> datetime | None:
    if args.from_date:
        return datetime.combine(
            date.fromisoformat(args.from_date), time.min, tzinfo=timezone.utc
        )
    if args.days and args.days > 0:
        return datetime.now(timezone.utc) - timedelta(days=args.days)
    return None


def _rss_sources(feeds: list[dict]) -> list[dict]:
    return [
        {
            "name": feed["name"],
            "kind": "rss",
            "reputation": feed.get("reputation", 0.5),
            "config": {"url": feed["url"]},
        }
        for feed in feeds
    ]


async def _known_urls(session) -> set[str]:
    return set((await session.scalars(select(Article.url))).all())


async def collect_news(
    session,
    since: datetime | None = None,
    entity_names: set[str] | None = None,
) -> int:
    client = LLMClient.from_settings()
    analyzer = ArticleAnalyzer(client)
    feeds = await get_rss_feeds(session) or DEFAULT_FEEDS
    source_ids, reputation = await ensure_sources(session, _rss_sources(feeds))

    print("Загрузка лент RSS...", flush=True)
    raw_articles = await fetch_rss_feeds(feeds)
    candidates = await filter_candidates(
        session,
        raw_articles,
        since,
        entity_names,
        await _known_urls(session),
        MAX_PER_FEED,
    )
    print(f"Загружено {len(raw_articles)} сообщений, "
          f"релевантных графу: {len(candidates)}", flush=True)

    stored = await ingest_candidates(
        session,
        candidates,
        source_ids,
        reputation,
        analyzer,
        f"llm:{client.model}",
    )
    return stored


async def collect_telegram_news(
    session,
    since: datetime | None = None,
    entity_names: set[str] | None = None,
    max_candidates: int = MAX_PER_FEED,
) -> int:
    settings = get_settings()
    channels = settings.telegram_channel_list
    if not settings.telegram_api_id or not settings.telegram_api_hash or not channels:
        print(
            "Telegram source disabled: set TELEGRAM_API_ID, TELEGRAM_API_HASH "
            "and TELEGRAM_CHANNELS in .env"
        )
        return 0

    client = LLMClient.from_settings()
    analyzer = ArticleAnalyzer(client)
    telegram_sources = [
        {
            "name": channel,
            "kind": "telegram",
            "reputation": 0.6,
            "config": {"channel": channel},
        }
        for channel in channels
    ]
    source_ids, reputation = await ensure_sources(session, telegram_sources)

    collector = TelegramCollector(
        settings.telegram_api_id, settings.telegram_api_hash, settings.telegram_session_name
    )
    print(f"Загрузка сообщений из Telegram-каналов: {', '.join(channels)}...", flush=True)
    raw_articles = await collector.fetch(channels, since=since)
    candidates = await filter_candidates(
        session,
        raw_articles,
        since,
        entity_names,
        await _known_urls(session),
        max_candidates,
    )
    print(
        f"Telegram: загружено {len(raw_articles)} сообщений, "
        f"релевантных графу: {len(candidates)}",
        flush=True,
    )

    stored = await ingest_candidates(
        session,
        candidates,
        source_ids,
        reputation,
        analyzer,
        f"llm:{client.model}",
    )
    return stored


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect, filter and analyze news via RSS (+ Telegram) and LLM"
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
    parser.add_argument(
        "--telegram",
        action="store_true",
        help="also collect news from Telegram channels (TELEGRAM_CHANNELS)",
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
        if args.telegram:
            tg_stored = await collect_telegram_news(
                session, since=since, entity_names=entity_names
            )
            print(f"Stored {tg_stored} analyzed Telegram articles")


if __name__ == "__main__":
    asyncio.run(main())
