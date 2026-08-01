from telegram import Bot

from app.config import get_settings

_bot_username: str | None = None


async def send_message(chat_id: int, text: str) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        return
    bot = Bot(token=settings.telegram_bot_token)
    try:
        await bot.initialize()
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    finally:
        await bot.shutdown()


async def get_bot_username() -> str | None:
    global _bot_username
    if _bot_username is not None:
        return _bot_username
    settings = get_settings()
    if not settings.telegram_bot_token:
        return None
    bot = Bot(token=settings.telegram_bot_token)
    try:
        await bot.initialize()
        me = await bot.get_me()
        _bot_username = me.username
    except Exception:
        return None
    finally:
        await bot.shutdown()
    return _bot_username
