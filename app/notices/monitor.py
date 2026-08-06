import asyncio
import os
import shutil
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select, text

from app.db.connection import SessionLocal
from app.db.models import (
    Alert,
    Article,
    MacroEvent,
    MarketCandle,
    Source,
    Strategy,
    SystemNotice,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOGS_DIR = PROJECT_ROOT / "logs"
MOEX_CHECK_URL = (
    "https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities.json"
    "?iss.meta=off&limit=1"
)
RSS_SAMPLE = [
    "https://www.rbc.ru/rss/",
    "https://www.interfax.ru/rss.asp",
]
DISK_MIN_FREE = 500 * 1024 * 1024

MONITOR_INTERVAL_SECONDS = 60
TIMEOUT_HTTP = 10

_last_run: dict[str, float] = {}


def is_fresh(value, days: int, now: date | None = None) -> bool:
    if value is None:
        return True
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone.utc).date()
    now = now or date.today()
    return value >= now - timedelta(days=days)


def friendly_error(exc: Exception, fallback: str) -> str:
    name = type(exc).__name__
    low = name.lower()
    if "timeout" in low:
        return f"{fallback}: таймаут соединения"
    if "network" in low:
        return f"{fallback}: сетевая ошибка"
    if "unauthorized" in low or "forbidden" in low:
        return f"{fallback}: неверный токен или нет прав доступа"
    if "connection" in low or "connect" in low:
        return f"{fallback}: нет соединения"
    return f"{fallback}: {name}: {exc}"[:300]


async def check_db() -> str | None:
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return None
    except Exception as exc:
        return friendly_error(exc, "База данных недоступна")


async def check_llm() -> str | None:
    from app.config import get_settings

    settings = get_settings()
    if not settings.llm_api_key:
        return "LLM_API_KEY не задан — анализ новостей недоступен"
    from app.llm.client import LLMClient

    try:
        client = LLMClient.from_settings()
        await client._client.models.list()
        return None
    except Exception as exc:
        return friendly_error(exc, "ИИ-анализ (LLM) недоступен")


async def check_telegram() -> str | None:
    from app.config import get_settings

    settings = get_settings()
    if not settings.telegram_bot_token:
        return None
    from telegram import Bot

    bot = Bot(token=settings.telegram_bot_token)
    try:
        await bot.initialize()
        await bot.get_me()
        return None
    except Exception as exc:
        return friendly_error(exc, "Telegram-бот недоступен")
    finally:
        try:
            await bot.shutdown()
        except Exception:
            pass


async def check_moex() -> str | None:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_HTTP, follow_redirects=True) as client:
            response = await client.get(MOEX_CHECK_URL)
        if response.status_code != 200:
            return f"MOEX ISS недоступен: HTTP {response.status_code}"
        return None
    except Exception as exc:
        return friendly_error(exc, "MOEX ISS недоступен")


async def check_rss() -> str | None:
    import httpx

    urls = list(RSS_SAMPLE)
    try:
        async with SessionLocal() as session:
            rows = await session.scalars(
                select(Source).where(Source.kind == "rss", Source.is_active.is_(True))
            )
            db_urls = [(s.config or {}).get("url") for s in rows]
            if db_urls:
                urls = db_urls
    except Exception:
        pass

    results = []
    async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
        for url in urls[:5]:
            try:
                response = await client.get(url)
                results.append(response.status_code)
            except Exception as exc:
                results.append(type(exc).__name__)
    if all(r != 200 for r in results):
        return f"RSS-источники недоступны: {results}"
    return None


async def check_disk() -> str | None:
    try:
        os.makedirs(LOGS_DIR, exist_ok=True)
        probe = LOGS_DIR / ".health-probe"
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
        os.remove(probe)
    except Exception as exc:
        return f"каталог логов недоступен: {type(exc).__name__}: {exc}"[:300]
    try:
        usage = shutil.disk_usage(PROJECT_ROOT)
        if usage.free < DISK_MIN_FREE:
            return f"мало свободного места: {usage.free // (1024 * 1024)} МБ"
    except Exception as exc:
        return f"ошибка проверки диска: {type(exc).__name__}"[:300]
    return None


async def _freshness_check(model, field: str, days: int, label: str) -> str | None:
    try:
        async with SessionLocal() as session:
            value = await session.scalar(select(func.max(getattr(model, field))))
        if not is_fresh(value, days):
            latest = value
            if isinstance(latest, datetime):
                latest = latest.astimezone(timezone.utc).date()
            return f"{label}: последнее обновление {latest:%d.%m.%Y}"
        return None
    except Exception as exc:
        return f"{label}: {type(exc).__name__}: {exc}"[:300]


async def check_pipeline_stale() -> str | None:
    return await _freshness_check(Article, "published_at", 3, "Новости")


async def check_stale_daily_pipeline() -> str | None:
    return await _freshness_check(Strategy, "generated_at", 2, "Ежедневный конвейер")


async def check_stale_prices() -> str | None:
    return await _freshness_check(MarketCandle, "trading_date", 3, "Обновление цен")


async def check_stale_alerts() -> str | None:
    return await _freshness_check(Alert, "created_at", 7, "Генерация алертов")


async def check_stale_macro() -> str | None:
    try:
        async with SessionLocal() as session:
            upcoming = await session.scalar(
                select(func.count())
                .select_from(MacroEvent)
                .where(MacroEvent.event_time >= datetime.now(timezone.utc))
            )
        if not upcoming:
            return "Макрокалендарь: нет предстоящих событий (не наполнен или устарел)"
        return None
    except Exception as exc:
        return f"Макрокалендарь: {type(exc).__name__}: {exc}"[:300]


CHECKS: list[tuple[str, object, str, int]] = [
    ("db", check_db, "critical", 120),
    ("llm", check_llm, "critical", 120),
    ("moex", check_moex, "critical", 120),
    ("telegram", check_telegram, "warning", 120),
    ("rss", check_rss, "warning", 3600),
    ("disk", check_disk, "warning", 600),
    ("pipeline_stale", check_pipeline_stale, "warning", 3600),
    ("stale_daily_pipeline", check_stale_daily_pipeline, "info", 3600),
    ("stale_prices", check_stale_prices, "info", 3600),
    ("stale_alerts", check_stale_alerts, "info", 3600),
    ("stale_macro", check_stale_macro, "info", 3600),
]


async def set_source_notice(
    session, source: str, level: str, text: str, active: bool
) -> None:
    existing = await session.scalar(
        select(SystemNotice).where(
            SystemNotice.source == source, SystemNotice.is_active.is_(True)
        )
    )
    if active:
        if existing is None:
            session.add(SystemNotice(level=level, text=text, source=source))
        elif existing.level != level or existing.text != text:
            existing.level = level
            existing.text = text
        await session.commit()
    elif existing is not None:
        existing.is_active = False
        await session.commit()


async def _apply_result(name: str, error: str | None, level: str) -> None:
    if error is None:
        try:
            async with SessionLocal() as session:
                await set_source_notice(session, name, level, "", active=False)
                if name == "db":
                    await set_source_notice(session, "db_outage", "critical", "", active=False)
        except Exception:
            pass
        return

    if name == "db":
        return
    try:
        async with SessionLocal() as session:
            await set_source_notice(session, name, level, error, active=True)
    except Exception:
        pass


async def monitor_once() -> dict:
    results: dict[str, str | None] = {}
    for name, check, _level, _cadence in CHECKS:
        try:
            results[name] = await check()
        except Exception as exc:
            results[name] = f"{type(exc).__name__}: {exc}"[:300]
        await _apply_result(name, results[name], _level)
    return results


async def run_monitor(interval: int = MONITOR_INTERVAL_SECONDS) -> None:
    while True:
        now = time.monotonic()
        for name, check, level, cadence in CHECKS:
            if now - _last_run.get(name, 0.0) >= cadence:
                try:
                    error = await check()
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"[:300]
                await _apply_result(name, error, level)
                _last_run[name] = now
        await asyncio.sleep(interval)
