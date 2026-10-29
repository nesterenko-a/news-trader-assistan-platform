from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from typing import Literal
from uuid import UUID
from pydantic import StrictBool
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.auth import get_current_user
from app.brokers import t_invest
from app.brokers import sandbox
from app.brokers import mcp
from app.brokers import mcp_llm
from app.db.connection import get_session
from app.db.models import BrokerConnection, User

router = APIRouter(prefix="/brokers/t-invest", tags=["brokers"])


class ConnectIn(BaseModel):
    token: str = Field(min_length=1, max_length=4096, json_schema_extra={"writeOnly": True}, repr=False)
    environment: Literal["production", "sandbox"] = "production"


class SelectAccountIn(BaseModel):
    account_id: str = Field(min_length=1, max_length=100)
    environment: Literal["production", "sandbox"] | None = None


class MCPCheckIn(BaseModel):
    environment: Literal["production", "sandbox"]


class MCPLLMCheckIn(BaseModel):
    provider: Literal["deepseek", "chatgpt"]
    model: str = Field(min_length=1, max_length=200)


@router.get("/mcp/models")
async def mcp_models(response: Response, provider: Literal["deepseek", "chatgpt"], user: User = Depends(get_current_user)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return await mcp_llm.catalog(provider)


@router.post("/mcp/check-llm")
async def mcp_check_llm(payload: MCPLLMCheckIn, request: Request, response: Response, user: User = Depends(get_current_user)) -> dict:
    _check_origin(request)
    response.headers["Cache-Control"] = "no-store"
    return await mcp_llm.check(payload.provider, payload.model, user.id)


@router.post("/mcp/check")
async def check_mcp(payload: MCPCheckIn, request: Request, response: Response, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    _check_origin(request)
    response.headers["Cache-Control"] = "no-store"
    connection = await t_invest.connection_for(session, user.id, environment=payload.environment)
    if connection is None:
        raise HTTPException(404, "Подключите выбранный контур Т-Инвестиций")
    connection_id, version = connection.id, connection.version
    result = await mcp.inspect_connection(t_invest.decrypt_token(connection), payload.environment)
    current = (await session.execute(select(BrokerConnection.id, BrokerConnection.version).where(BrokerConnection.user_id == user.id, BrokerConnection.provider == "t_invest", BrokerConnection.environment == payload.environment))).first()
    if current is None or current.id != connection_id or current.version != version:
        raise HTTPException(409, "Подключение изменилось. Повторите проверку MCP")
    return result


class SandboxCreateIn(BaseModel):
    operation_key: UUID
    name: str = Field(default="", max_length=100)


class SandboxPayIn(BaseModel):
    operation_key: UUID
    amount: str = Field(min_length=1, max_length=30)


class SandboxCloseIn(BaseModel):
    operation_key: UUID
    confirmed: StrictBool


def _check_origin(request: Request) -> None:
    if request.cookies.get("nt_token"):
        origin = request.headers.get("origin")
        host = request.headers.get("host")
        if not origin or not host or origin.removeprefix("https://").removeprefix("http://") != host:
            raise HTTPException(403, "Недопустимый источник запроса")


@router.get("")
async def status(response: Response, environment: Literal["production", "sandbox"] | None = None, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    return await t_invest.view(session, user.id, environment=environment)


@router.post("/connect")
async def connect(payload: ConnectIn, request: Request, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    _check_origin(request)
    await t_invest.connect(session, user.id, payload.token, environment=payload.environment)
    return await t_invest.view(session, user.id, environment=payload.environment)


@router.post("/select-account")
async def select_account(payload: SelectAccountIn, request: Request, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    _check_origin(request)
    await t_invest.refresh(session, user.id, payload.account_id, environment=payload.environment)
    return await t_invest.view(session, user.id, environment=payload.environment)


@router.post("/refresh")
async def refresh(request: Request, environment: Literal["production", "sandbox"] | None = None, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    _check_origin(request)
    await t_invest.refresh(session, user.id, environment=environment)
    return await t_invest.view(session, user.id, environment=environment)


@router.get("/portfolio")
async def portfolio(response: Response, environment: Literal["production", "sandbox"] | None = None, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    response.headers["Cache-Control"] = "no-store"
    return await t_invest.view(session, user.id, environment=environment)


@router.delete("")
async def disconnect(request: Request, environment: Literal["production", "sandbox"] | None = None, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    _check_origin(request)
    await t_invest.disconnect(session, user.id, environment=environment)
    return {"connected": False}


@router.get("/sandbox/accounts")
async def sandbox_accounts(response: Response, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return await sandbox.list_accounts(session, user.id)


@router.post("/sandbox/accounts")
async def sandbox_create(payload: SandboxCreateIn, request: Request, response: Response, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    _check_origin(request)
    response.headers["Cache-Control"] = "no-store"
    return await sandbox.execute(session, user.id, "create", payload.operation_key, name=payload.name)


@router.post("/sandbox/accounts/{account_id}/pay-in")
async def sandbox_pay_in(account_id: str, payload: SandboxPayIn, request: Request, response: Response, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    _check_origin(request)
    response.headers["Cache-Control"] = "no-store"
    return await sandbox.execute(session, user.id, "pay_in", payload.operation_key, account_id=account_id, amount=payload.amount)


@router.post("/sandbox/accounts/{account_id}/close")
async def sandbox_close(account_id: str, payload: SandboxCloseIn, request: Request, response: Response, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> dict:
    _check_origin(request)
    response.headers["Cache-Control"] = "no-store"
    return await sandbox.execute(session, user.id, "close", payload.operation_key, account_id=account_id, confirmed=payload.confirmed)
