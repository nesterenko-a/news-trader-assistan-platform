import asyncio
import logging

import uvicorn

from app.bot.application import build_application
from app.config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("newstrader")

HOST = "0.0.0.0"
PORT = 8000


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

    bot = None
    if settings.telegram_bot_token:
        bot = build_application()
        await bot.initialize()
        await bot.start()
        await bot.updater.start_polling()
        logger.info("Telegram bot started")
    else:
        logger.warning("TELEGRAM_BOT_TOKEN is not set — Telegram bot skipped")

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
