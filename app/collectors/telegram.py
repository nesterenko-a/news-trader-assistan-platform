import re
from datetime import datetime, timezone

from telethon import TelegramClient

from app.collectors.rss import RawArticle

MIN_TEXT_LENGTH = 40
MAX_TITLE_LENGTH = 80
DEFAULT_LIMIT_PER_CHANNEL = 200


class TelegramAuthError(RuntimeError):
    pass


def _make_title(text: str) -> str:
    sentences = re.split(r"(?<=[.!?…])\s+", text.strip())
    title = sentences[0] if sentences else text
    if len(title) > MAX_TITLE_LENGTH:
        title = title[:MAX_TITLE_LENGTH].rstrip()
    return title or text[:MAX_TITLE_LENGTH]


def _to_aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class TelegramCollector:
    def __init__(self, api_id: str, api_hash: str, session_name: str = "telethon_session"):
        self._api_id = api_id
        self._api_hash = api_hash
        self._session_name = session_name

    async def fetch(
        self,
        channels: list[str],
        since: datetime | None = None,
        limit_per_channel: int = DEFAULT_LIMIT_PER_CHANNEL,
    ) -> list[RawArticle]:
        articles: list[RawArticle] = []
        client = TelegramClient(
            self._session_name, int(self._api_id), self._api_hash
        )
        await client.connect()
        try:
            if not await client.is_user_authorized():
                raise TelegramAuthError(
                    "Telethon не авторизован. Выполните первый вход: "
                    "python -m scripts.telegram_login"
                )
            for channel in channels:
                try:
                    entity = await client.get_entity(channel)
                except Exception:
                    continue
                async for message in client.iter_messages(
                    entity, limit=limit_per_channel
                ):
                    published = _to_aware_utc(message.date)
                    if since is not None and published is not None and published < since:
                        break
                    text = (message.text or "").strip()
                    if len(text) < MIN_TEXT_LENGTH:
                        continue
                    message_id = getattr(message.id, "id", message.id)
                    articles.append(
                        RawArticle(
                            title=_make_title(text),
                            text=text,
                            url=f"https://t.me/{channel}/{message_id}",
                            source_name=channel,
                            published_at=published or datetime.now(timezone.utc),
                        )
                    )
        finally:
            await client.disconnect()
        return articles
