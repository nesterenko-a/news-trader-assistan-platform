from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

from app.bot.handlers import handle_text, help_command, start
from app.config import get_settings


def build_application():
    settings = get_settings()
    application = ApplicationBuilder().token(settings.telegram_bot_token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
    )
    return application


def main() -> None:
    application = build_application()
    application.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
