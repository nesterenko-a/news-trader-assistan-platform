"""Проверка работоспособности RSS-лент и защита от SSRF."""

import asyncio
import ipaddress
from urllib.parse import urlparse

import feedparser
import httpx

MAX_FEED_BYTES = 2 * 1024 * 1024
HTTP_TIMEOUT = 10.0
TOTAL_TIMEOUT = 30.0
MAX_REDIRECTS = 5


async def validate_feed_url(url: str) -> str | None:
    """Возвращает текст ошибки, если URL небезопасен/невалиден, иначе None.

    Асинхронная версия: DNS-резолв через event loop (не блокирует его).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "Допустимы только http/https"
    host = parsed.hostname
    if not host:
        return "Неверный URL"
    if host == "localhost" or host.endswith(".localhost"):
        return "Локальные адреса запрещены"
    try:
        port = parsed.port or 80
    except ValueError:
        return "Неверный порт"
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port)
    except OSError:
        return "Не удалось разрешить хост"
    for info in infos:
        try:
            # IPv6 zone-идентификатор (fe80::1%eth0) — проверяем сам адрес
            ip = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        except ValueError:
            # Fail-closed: нераспознанный адрес не должен проходить проверку
            return "Некорректный адрес"
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return "Внутренние адреса запрещены"
    return None


async def fetch_feed_bytes(url: str) -> tuple[bytes | None, str]:
    """Безопасный фетч ленты: SSRF-валидация на каждом хопе редиректов,
    без автоматического следования, лимит размера при потоковом чтении, таймаут.

    Возвращает (bytes, "ok") или (None, описание ошибки).
    """
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        error = await validate_feed_url(current)
        if error:
            return None, error
        try:
            async with asyncio.timeout(TOTAL_TIMEOUT):
                async with httpx.AsyncClient(
                    timeout=HTTP_TIMEOUT, follow_redirects=False
                ) as client:
                    async with client.stream("GET", current) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            location = response.headers.get("location")
                            if not location:
                                return None, "Редирект без Location"
                            current = str(httpx.URL(current).join(location))
                            continue
                        if response.status_code != 200:
                            return None, f"HTTP {response.status_code}"
                        chunks: list[bytes] = []
                        size = 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > MAX_FEED_BYTES:
                                return None, "Ответ слишком большой"
                            chunks.append(chunk)
                        return b"".join(chunks), "ok"
        except TimeoutError:
            return None, "Таймаут соединения"
        except httpx.TimeoutException:
            return None, "Таймаут соединения"
        except httpx.HTTPError as exc:
            return None, f"Сетевая ошибка: {type(exc).__name__}"
    return None, "Слишком много редиректов"


async def check_feed(url: str) -> tuple[bool, str]:
    """Проверяет ленту: (ok, описание). ok=False при любой ошибке."""
    data, error = await fetch_feed_bytes(url)
    if data is None:
        return False, error
    parsed = feedparser.parse(data)
    if parsed.get("bozo") and not parsed.entries:
        reason = parsed.get("bozo_exception")
        return False, f"Не похоже на RSS: {type(reason).__name__ if reason else 'parse error'}"
    if not parsed.entries:
        return False, "В ленте нет записей"
    return True, "ok"
