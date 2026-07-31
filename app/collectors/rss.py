from dataclasses import dataclass
from datetime import datetime, timezone

import feedparser


@dataclass
class RawArticle:
    title: str
    text: str
    url: str
    source_name: str
    published_at: datetime | None


DEFAULT_FEEDS = [
    {"name": "РБК", "url": "https://rssexport.rbc.ru/exports/full/rbc/news.rss", "reputation": 0.8},
    {"name": "Интерфакс", "url": "https://www.interfax.ru/rss.asp", "reputation": 0.8},
    {"name": "ТАСС", "url": "https://tass.ru/rss/v2.xml", "reputation": 0.8},
    {"name": "Коммерсантъ", "url": "https://www.kommersant.ru/RSS/news.xml", "reputation": 0.75},
    {"name": "Ведомости", "url": "https://www.vedomosti.ru/rss/news", "reputation": 0.8},
    {"name": "Прайм", "url": "https://1prime.ru/export/rss2/index.xml", "reputation": 0.8},
    {"name": "Forbes Россия", "url": "https://www.forbes.ru/feed", "reputation": 0.75},
    {"name": "БКС Экспресс", "url": "https://bcs-express.ru/rss/news", "reputation": 0.7},
    {"name": "Финам", "url": "https://www.finam.ru/rss/", "reputation": 0.7},
    {"name": "Smart-lab", "url": "https://smart-lab.ru/rss/", "reputation": 0.65},
]


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = feedparser.parse(value)
        dt = parsed.get("published_parsed")
        return datetime.fromtimestamp(feedparser.mktime(dt)) if dt else None
    except Exception:
        return None


class RSSCollector:
    def __init__(self, feeds: list[dict] | None = None):
        self.feeds = feeds or DEFAULT_FEEDS

    def fetch(self) -> list[RawArticle]:
        articles: list[RawArticle] = []
        for feed in self.feeds:
            try:
                parsed = feedparser.parse(feed["url"])
            except Exception:
                continue
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
