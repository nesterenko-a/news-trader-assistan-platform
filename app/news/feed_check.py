"""Проверка работоспособности RSS-лент и защита от SSRF."""

import ipaddress
import socket
from urllib.parse import urlparse

import feedparser
import httpx

MAX_FEED_BYTES = 2 * 1024 * 1024
HTTP_TIMEOUT = 10.0


def validate_feed_url(url: str) -> str | None:
    """Возвращает текст ошибки, если URL небезопасен/невалиден, иначе None."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "Допустимы только http/https"
    host = parsed.hostname
    if not host:
        return "Неверный URL"
    if host == "localhost" or host.endswith(".localhost"):
        return "Локальные адреса запрещены"
    try:
        infos = socket.getaddrinfo(host, parsed.port or 80)
    except OSError:
        return "Не удалось разрешить хост"
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
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


async def check_feed(url: str) -> tuple[bool, str]:
    """Проверяет ленту: (ok, описание). ok=False при любой ошибке."""
    error = validate_feed_url(url)
    if error:
        return False, error
    try:
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT, follow_redirects=True
        ) as client:
            response = await client.get(url)
    except httpx.TimeoutException:
        return False, "Таймаут соединения"
    except httpx.HTTPError as exc:
        return False, f"Сетевая ошибка: {type(exc).__name__}"
    if response.status_code != 200:
        return False, f"HTTP {response.status_code}"
    if len(response.content) > MAX_FEED_BYTES:
        return False, "Ответ слишком большой"
    parsed = feedparser.parse(response.content)
    if parsed.get("bozo") and not parsed.entries:
        reason = parsed.get("bozo_exception")
        return False, f"Не похоже на RSS: {type(reason).__name__ if reason else 'parse error'}"
    if not parsed.entries:
        return False, "В ленте нет записей"
    return True, "ok"
