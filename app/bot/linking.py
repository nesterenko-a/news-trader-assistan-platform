import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TelegramLinkCode, User

LINK_CODE_TTL = timedelta(minutes=15)
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _generate_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))


async def create_link_code(session: AsyncSession, chat_id: int) -> str:
    code = _generate_code()
    session.add(
        TelegramLinkCode(
            code=code,
            chat_id=chat_id,
            expires_at=datetime.now(timezone.utc) + LINK_CODE_TTL,
        )
    )
    await session.commit()
    return code


async def consume_link_code(session: AsyncSession, code: str) -> int | None:
    record = await session.scalar(
        select(TelegramLinkCode).where(TelegramLinkCode.code == code.upper())
    )
    if record is None:
        return None
    expires = record.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        await session.delete(record)
        await session.commit()
        return None
    chat_id = record.chat_id
    await session.delete(record)
    await session.commit()
    return chat_id


async def set_user_chat(session: AsyncSession, user_id: int, chat_id: int) -> User:
    user = await session.get(User, user_id)
    user.telegram_chat_id = chat_id
    await session.commit()
    return user


async def unlink_telegram(session: AsyncSession, user_id: int) -> None:
    user = await session.get(User, user_id)
    if user is not None and user.telegram_chat_id is not None:
        user.telegram_chat_id = None
        await session.commit()


async def cleanup_expired_codes(session: AsyncSession) -> int:
    result = await session.execute(
        delete(TelegramLinkCode).where(
            TelegramLinkCode.expires_at < datetime.now(timezone.utc)
        )
    )
    await session.commit()
    return result.rowcount or 0
