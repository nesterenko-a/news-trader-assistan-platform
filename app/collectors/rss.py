from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import asyncio

import feedparser

from app.news.feed_check import fetch_feed_bytes


@dataclass
class RawArticle:
    title: str
    text: str
    url: str
    source_name: str
    published_at: datetime | None


DEFAULT_FEEDS = [
    # РБК закрыл старый rssexport (404), канонический адрес отдаёт 401 ботам (Qrator) — лента честно помечается красной в менеджере источников.
    {"name": "РБК", "url": "https://www.rbc.ru/rss/", "reputation": 0.8, "category": "финансы"},
    {"name": "Интерфакс", "url": "https://www.interfax.ru/rss.asp", "reputation": 0.8, "category": "финансы"},
    {"name": "ТАСС", "url": "https://tass.ru/rss/v2.xml", "reputation": 0.8, "category": "финансы"},
    {"name": "Коммерсантъ", "url": "https://www.kommersant.ru/RSS/news.xml", "reputation": 0.75, "category": "финансы"},
    {"name": "Ведомости", "url": "https://www.vedomosti.ru/rss/news", "reputation": 0.8, "category": "финансы"},
    {"name": "Прайм", "url": "https://1prime.ru/export/rss2/index.xml", "reputation": 0.8, "category": "финансы"},
    {"name": "Forbes Россия", "url": "https://www.forbes.ru/feed", "reputation": 0.75, "category": "финансы"},
    {"name": "БКС Экспресс", "url": "https://bcs-express.ru/rss/news", "reputation": 0.7, "category": "финансы"},
    {"name": "Финам", "url": "https://www.finam.ru/rss/", "reputation": 0.7, "category": "финансы"},
    {"name": "Smart-lab", "url": "https://smart-lab.ru/rss/", "reputation": 0.65, "category": "финансы"},
]


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        pass
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


class RSSCollector:
    """Синхронный сбор из переданного списка лент (legacy, без сетевых ограничений).

    Для продакшн-конвейера используйте async fetch_rss_feeds() — он применяет
    SSRF-валидацию, таймауты и лимит размера.
    """

    def __init__(self, feeds: list[dict] | None = None):
        self.feeds = feeds or DEFAULT_FEEDS

    def fetch(self) -> list[RawArticle]:
        articles: list[RawArticle] = []
        for feed in self.feeds:
            try:
                parsed = feedparser.parse(feed["url"])
            except Exception:
                continue
            articles.extend(_parsed_entries(feed, parsed))
        return articles


def _parsed_entries(feed: dict, parsed) -> list[RawArticle]:
    articles: list[RawArticle] = []
    for entry in parsed.entries:
        title = entry.get("title", "").strip()
        if not title:
            continue
        text = entry.get("summary", "") or entry.get("description", "") or ""
        link = entry.get("link", "").strip()
        published = _parse_date(entry.get("published", ""))
        articles.append(
            RawArticle(
                title=title,
                text=text.strip(),
                url=link,
                source_name=feed["name"],
                published_at=published or datetime.now(timezone.utc),
            )
        )
    return articles


async def fetch_rss_feeds(feeds: list[dict]) -> list[RawArticle]:
    """Безопасный асинхронный сбор: фетч с SSRF-валидацией на каждом хопе,
    таймаутом 10 с и лимитом 2 МБ; ленты с ошибками пропускаются."""
    results = await asyncio.gather(
        *(fetch_feed_bytes(feed["url"]) for feed in feeds)
    )
    articles: list[RawArticle] = []
    for feed, (data, error) in zip(feeds, results):
        if data is None:
            print(f"  лента {feed['name']}: {error}", flush=True)
            continue
        parsed = feedparser.parse(data)
        articles.extend(_parsed_entries(feed, parsed))
    return articles
