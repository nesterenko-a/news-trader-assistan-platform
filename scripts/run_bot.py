from app.bot.application import build_application
from app.db.migrations import run_migrations


def main() -> None:
    run_migrations()
    build_application().run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
