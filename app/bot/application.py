from telegram import BotCommand
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
)

from app.bot.handlers import handle_text, help_command, start
from app.config import get_settings

MENU_COMMANDS = [
    BotCommand("start", "Приветствие и инструкция"),
    BotCommand("help", "Справка по командам"),
]


async def apply_menu(application: Application) -> None:
    await application.bot.set_my_commands(MENU_COMMANDS)


async def _post_init(application: Application) -> None:
    await apply_menu(application)


def build_application() -> Application:
    settings = get_settings()
    application = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .post_init(_post_init)
        .build()
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
    )
    return application
