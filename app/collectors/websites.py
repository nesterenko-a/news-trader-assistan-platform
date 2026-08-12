"""Сбор новостей со страниц-списков сайтов компаний (источник kind='website')."""

import asyncio

from app.collectors.rss import RawArticle, _entries_to_articles
from app.news.feed_check import fetch_feed_bytes


async def fetch_website(site: dict) -> list[RawArticle]:
    """Безопасный сбор записей со страницы-списка сайта.

    Фетч с SSRF-валидацией, таймаутом и лимитом размера (fetch_feed_bytes);
    при недоступности по HTTP и use_browser — фолбэк через Playwright;
    извлечение записей — через LLM (parse_site_with_llm, требуется use_llm).
    Ошибки не роняют сбор: лог в stdout и пустой результат.
    """
    from app.news.browser_fetch import fetch_with_playwright
    from app.news.llm_parse import parse_site_with_llm

    data, error = await fetch_feed_bytes(site["url"])
    if data is None and site.get("use_browser"):
        try:
            data = await fetch_with_playwright(site["url"])
        except Exception:
            data = None
    if data is None:
        print(f"  сайт {site['name']}: {error}", flush=True)
        return []
    if not site.get("use_llm"):
        print(
            f"  сайт {site['name']}: LLM-разбор выключен, записей не извлечено",
            flush=True,
        )
        return []
    try:
        entries = await parse_site_with_llm(data)
    except Exception:
        entries = []
    if not entries:
        print(f"  сайт {site['name']}: LLM не извлёк записи", flush=True)
        return []
    print(f"  сайт {site['name']}: {len(entries)} записей через LLM", flush=True)
    return _entries_to_articles(site, entries)


async def fetch_websites(sites: list[dict]) -> list[RawArticle]:
    """Сбор по всем сайтам параллельно; ленты с ошибками пропускаются."""
    if not sites:
        return []
    results = await asyncio.gather(*(fetch_website(site) for site in sites))
    articles: list[RawArticle] = []
    for site, items in zip(sites, results):
        articles.extend(items)
    return articles
