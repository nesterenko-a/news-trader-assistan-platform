from app.bot.application import build_application


def main() -> None:
    build_application().run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
