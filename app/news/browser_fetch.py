"""Фетч лент через Playwright (headless Chromium) — обход JS-челленджей
антибота (ServicePipe и подобных): браузер выполняет JS, получает cookie,
после чего повторный запрос отдаёт настоящий контент ленты.

Используется как fallback-уровень для источников с галочкой
«Обход антибота (браузер)» (sources.use_browser).
"""

import asyncio

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
CHALLENGE_TIMEOUT = 45.0
PAGE_TIMEOUT = 30.0

# Появление этих cookie означает, что JS-челлендж пройден
_CHALLENGE_COOKIES = {"spid", "spsn", "spsc"}


def _looks_like_feed(body: bytes) -> bool:
    """Признак настоящей ленты: маркеры RSS/Atom в начале тела.

    Заглушка антибота (HTML-страница челленджа) их не содержит.
    """
    head = body[:8192].lower()
    return any(m in head for m in (b"<rss", b"<feed", b"<item", b"<entry"))


async def fetch_with_playwright(url: str) -> bytes | None:
    """Открывает URL в headless-Chromium, дожидается прохождения
    JS-челленджа (cookie) и повторно запрашивает страницу с cookie.

    Повторные запросы делаются, пока тело не станет похоже на ленту
    (иногда челлендж требует нескольких раундов). Возвращает сырое
    тело последнего ответа или None при неудаче/таймауте.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return None

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                user_agent=USER_AGENT, locale="ru-RU"
            )
            page = await context.new_page()
            await page.goto(
                url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT * 1000
            )

            deadline = asyncio.get_running_loop().time() + CHALLENGE_TIMEOUT
            while True:
                cookies = {
                    c["name"] for c in await context.cookies()
                }
                if _CHALLENGE_COOKIES & cookies:
                    resp = await page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=PAGE_TIMEOUT * 1000,
                    )
                    if resp is not None:
                        body = await resp.body()
                        if body and _looks_like_feed(body):
                            return bytes(body)
                if asyncio.get_running_loop().time() >= deadline:
                    return None
                await asyncio.sleep(2)
        finally:
            await browser.close()
