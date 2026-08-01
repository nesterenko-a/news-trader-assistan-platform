from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    create_session,
    delete_session,
    get_current_user,
    hash_password,
    verify_password,
)
from app.db.connection import get_session
from app.db.models import User
from app.schemas import AuthOut, LoginIn, RegisterIn, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_from_request(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get("nt_token")


@router.post("/register", response_model=AuthOut, status_code=201)
async def register(
    payload: RegisterIn,
    session: AsyncSession = Depends(get_session),
) -> dict:
    username = payload.username.strip()
    if len(username) < 3:
        raise HTTPException(status_code=400, detail="Имя пользователя слишком короткое")
    if len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="Пароль слишком короткий (мин. 6 символов)")
    existing = await session.scalar(select(User).where(User.username == username))
    if existing is not None:
        raise HTTPException(status_code=409, detail="Пользователь уже существует")
    user = User(username=username, password_hash=hash_password(payload.password))
    session.add(user)
    await session.flush()
    token = await create_session(session, user)
    return {"token": token, "username": user.username}


@router.post("/login", response_model=AuthOut)
async def login(
    payload: LoginIn,
    session: AsyncSession = Depends(get_session),
) -> dict:
    user = await session.scalar(select(User).where(User.username == payload.username.strip()))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Неверное имя пользователя или пароль")
    token = await create_session(session, user)
    return {"token": token, "username": user.username}


@router.post("/logout")
async def logout(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    token = _token_from_request(request)
    if token:
        await delete_session(session, token)
    return {"status": "ok"}


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> dict:
    return {"id": user.id, "username": user.username, "role": user.role}
