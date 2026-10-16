from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from typing import Literal
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.brokers import t_invest
from app.db.connection import get_session
from app.db.models import User

router = APIRouter(prefix="/brokers/t-invest", tags=["brokers"])


class ConnectIn(BaseModel):
    token: str = Field(min_length=1, max_length=4096, json_schema_extra={"writeOnly": True}, repr=False)
    environment: Literal["production", "sandbox"] = "production"


class SelectAccountIn(BaseModel):
    account_id: str = Field(min_length=1, max_length=100)


def _check_origin(request: Request) -> None:
    if request.cookies.get("nt_token"):
        origin = request.headers.get("origin")
        host = request.headers.get("host")
        if not origin or not host or origin.removeprefix("https://").removeprefix("http://") != host:
            raise HTTPException(403, "Недопустимый источник запроса")


@router.get("")
async def status(response: Response, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    return await t_invest.view(session, user.id)


@router.post("/connect")
async def connect(payload: ConnectIn, request: Request, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    _check_origin(request)
    await t_invest.connect(session, user.id, payload.token, environment=payload.environment)
    return await t_invest.view(session, user.id)


@router.post("/select-account")
async def select_account(payload: SelectAccountIn, request: Request, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    _check_origin(request)
    await t_invest.refresh(session, user.id, payload.account_id)
    return await t_invest.view(session, user.id)


@router.post("/refresh")
async def refresh(request: Request, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    _check_origin(request)
    await t_invest.refresh(session, user.id)
    return await t_invest.view(session, user.id)


@router.get("/portfolio")
async def portfolio(response: Response, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    return await t_invest.view(session, user.id)


@router.delete("")
async def disconnect(request: Request, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    _check_origin(request)
    await t_invest.disconnect(session, user.id)
    return {"connected": False}
