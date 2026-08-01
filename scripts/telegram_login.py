import asyncio

from telethon import TelegramClient

from app.config import get_settings


async def main() -> None:
    settings = get_settings()
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        print("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env")
        return
    client = TelegramClient(
        settings.telegram_session_name, int(settings.telegram_api_id), settings.telegram_api_hash
    )
    await client.connect()
    try:
        if await client.is_user_authorized():
            print("Telethon уже авторизован")
        else:
            await client.start()
            print("Telethon авторизован")
        me = await client.get_me()
        print("Аккаунт:", me.username or me.first_name)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
