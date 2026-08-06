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
            entries = [
                {
                    "title": e.get("title", ""),
                    "link": e.get("link", ""),
                    "description": e.get("summary", "")
                    or e.get("description", ""),
                    "published": e.get("published", ""),
                }
                for e in parsed.entries
            ]
            articles.extend(_entries_to_articles(feed, entries))
        return articles



def _entries_to_articles(feed: dict, entries: list[dict]) -> list[RawArticle]:
    articles: list[RawArticle] = []
    for entry in entries:
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        text = (entry.get("description") or "")[:4000]
        link = (entry.get("link") or "").strip()
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
    таймаутом 10 с и лимитом 2 МБ; ленты с ошибками пропускаются.

    Парсинг — конвейером (feedparser → толерантный HTML/XML-парсер); если
    записи не извлечены и у ленты включён use_llm — пробуется LLM-разбор.
    """
    from app.news.browser_fetch import fetch_with_playwright
    from app.news.feed_parsers import parse_feed
    from app.news.llm_parse import parse_feed_with_llm

    results = await asyncio.gather(
        *(fetch_feed_bytes(feed["url"]) for feed in feeds)
    )
    articles: list[RawArticle] = []
    for feed, (data, error) in zip(feeds, results):
        if data is None:
            print(f"  лента {feed['name']}: {error}", flush=True)
            continue
        result = parse_feed(data)
        entries = result.entries
        if not entries and feed.get("use_browser"):
            try:
                browser_data = await fetch_with_playwright(feed["url"])
            except Exception:
                browser_data = None
            if browser_data:
                browser_result = parse_feed(browser_data)
                entries = browser_result.entries
                if entries:
                    print(
                        f"  лента {feed['name']}: {len(entries)} записей через браузер",
                        flush=True,
                    )
        if not entries and feed.get("use_llm"):
            try:
                entries = await parse_feed_with_llm(data)
            except Exception:
                entries = []
            if entries:
                print(f"  лента {feed['name']}: {len(entries)} записей через LLM", flush=True)
        if not entries and result.error:
            print(
                f"  лента {feed['name']}: не распознана ({result.error})",
                flush=True,
            )
        articles.extend(_entries_to_articles(feed, entries))
    return articles
