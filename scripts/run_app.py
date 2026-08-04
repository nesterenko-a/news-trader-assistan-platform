import asyncio
import logging

import uvicorn

from app.bot.application import apply_menu, build_application
from app.config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("newstrader")

HOST = "0.0.0.0"
PORT = 8000


async def _start_bot():
    settings = get_settings()
    if not settings.telegram_bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN is not set — Telegram bot skipped")
        return None
    bot = build_application()
    try:
        await bot.initialize()
        await bot.start()
        await bot.updater.start_polling()
        await apply_menu(bot)
    except Exception as exc:
        logger.error("Telegram bot failed to start: %s", exc)
        try:
            await bot.shutdown()
        except Exception:
            pass
        try:
            from app.notices.service import notify_telegram_unavailable

            await notify_telegram_unavailable(str(exc))
        except Exception:
            pass
        return None
    logger.info("Telegram bot started")
    return bot


async def main() -> None:
    settings = get_settings()

    server = uvicorn.Server(
        uvicorn.Config(
            "app.main:app",
            host=HOST,
            port=PORT,
            log_level="info",
        )
    )

    bot = await _start_bot()

    try:
        await server.serve()
    finally:
        if bot is not None:
            await bot.updater.stop()
            await bot.stop()
            await bot.shutdown()
            logger.info("Telegram bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
