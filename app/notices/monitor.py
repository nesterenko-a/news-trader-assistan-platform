import asyncio
from datetime import datetime, timezone

from sqlalchemy import select, text

from app.db.connection import SessionLocal
from app.db.models import SystemNotice

MONITOR_INTERVAL_SECONDS = 120

_db_down_since: datetime | None = None


async def check_db() -> str | None:
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return None
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"[:300]


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
        return f"{type(exc).__name__}: {exc}"[:300]


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
        return f"{type(exc).__name__}: {exc}"[:300]
    finally:
        try:
            await bot.shutdown()
        except Exception:
            pass


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


async def monitor_once() -> dict:
    global _db_down_since
    results: dict[str, str | None] = {}
    for name, check in (
        ("db", check_db),
        ("llm", check_llm),
        ("telegram", check_telegram),
    ):
        try:
            results[name] = await check()
        except Exception as exc:
            results[name] = f"{type(exc).__name__}: {exc}"[:300]

    for name, error in results.items():
        if error is None:
            try:
                async with SessionLocal() as session:
                    await set_source_notice(session, f"{name}_health", "warning", "", active=False)
            except Exception:
                pass
            if name == "db" and _db_down_since is not None:
                text = (
                    "База данных была недоступна с "
                    f"{_db_down_since.strftime('%d.%m %H:%M')} (восстановлено)"
                )
                try:
                    async with SessionLocal() as session:
                        await set_source_notice(
                            session, "db_outage", "critical", text, active=True
                        )
                except Exception:
                    pass
                _db_down_since = None
        else:
            if name == "db":
                if _db_down_since is None:
                    _db_down_since = datetime.now(timezone.utc)
                continue
            level = "warning" if name == "telegram" else "critical"
            label = {
                "llm": "ИИ-анализ (LLM)",
                "telegram": "Telegram-бот",
            }[name]
            try:
                async with SessionLocal() as session:
                    await set_source_notice(
                        session,
                        f"{name}_health",
                        level,
                        f"Нет подключения к {label}: {error}",
                        active=True,
                    )
            except Exception:
                pass
    return results


async def run_monitor(interval: int = MONITOR_INTERVAL_SECONDS) -> None:
    while True:
        try:
            await monitor_once()
        except Exception:
            pass
        await asyncio.sleep(interval)
