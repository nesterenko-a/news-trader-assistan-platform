class TelegramCollector:
    def __init__(self, api_id: str, api_hash: str):
        self._api_id = api_id
        self._api_hash = api_hash

    async def fetch(self, channels: list[str]) -> list[dict]:
        raise NotImplementedError(
            "Telegram-каналы подключаются на этапе 1: требуется Telethon, "
            "api_id/api_hash и список каналов"
        )
