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
CHALLENGE_TIMEOUT = 30.0
PAGE_TIMEOUT = 30.0

# Появление этих cookie означает, что JS-челлендж пройден
_CHALLENGE_COOKIES = {"spid", "spsn", "spsc"}


async def fetch_with_playwright(url: str) -> bytes | None:
    """Открывает URL в headless-Chromium, дожидается прохождения
    JS-челленджа (cookie) и повторно запрашивает страницу с cookie.

    Возвращает сырое тело последнего ответа или None при неудаче/таймауте.
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
            while asyncio.get_running_loop().time() < deadline:
                cookies = {
                    c["name"]: c["value"] for c in await context.cookies()
                }
                if _CHALLENGE_COOKIES & set(cookies):
                    break
                await asyncio.sleep(1)

            resp = await page.goto(
                url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT * 1000
            )
            if resp is None:
                return None
            body = await resp.body()
            return bytes(body) if body else None
        finally:
            await browser.close()
