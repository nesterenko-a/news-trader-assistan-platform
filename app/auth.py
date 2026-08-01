import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.connection import get_session
from app.db.models import Session, User

PBKDF2_ITERATIONS = 200_000
SESSION_TTL = timedelta(days=30)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), PBKDF2_ITERATIONS
    ).hex()
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, digest = stored.split("$")
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt.encode(), int(iterations)
        ).hex()
        return hmac.compare_digest(candidate, digest)
    except ValueError:
        return False


async def create_session(session: AsyncSession, user: User) -> str:
    token = secrets.token_urlsafe(48)
    record = Session(
        user_id=user.id,
        token=token,
        expires_at=datetime.now(timezone.utc) + SESSION_TTL,
    )
    session.add(record)
    await session.commit()
    return token


async def delete_session(session: AsyncSession, token: str) -> None:
    record = await session.scalar(select(Session).where(Session.token == token))
    if record is not None:
        await session.delete(record)
        await session.commit()


def _extract_token(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get("nt_token")


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> User:
    token = _extract_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется авторизация"
        )
    record = await session.scalar(select(Session).where(Session.token == token))
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Сессия недействительна"
        )
    expires = record.expires_at
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires is not None and expires < datetime.now(timezone.utc):
        await delete_session(session, token)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Сессия истекла"
        )
    user = await session.get(User, record.user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Пользователь не найден"
        )
    return user
