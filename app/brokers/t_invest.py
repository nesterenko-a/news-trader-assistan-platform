import asyncio
import base64
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.brokers.certificates import tls_context
from app.db.models import (
    BrokerAccount,
    BrokerConnection,
    BrokerConnectionEvent,
    BrokerPortfolioSnapshot,
    BrokerSandboxAction,
    Security,
)

BASE = "https://invest-public-api.tbank.ru/rest/tinkoff.public.invest.api.contract.v1"
SANDBOX_BASE = "https://sandbox-invest-public-api.tbank.ru/rest/tinkoff.public.invest.api.contract.v1"
READ_ONLY = "ACCOUNT_ACCESS_LEVEL_READ_ONLY"


def _keys() -> dict[str, bytes]:
    raw = get_settings().broker_token_keys
    result = {}
    for item in raw.split(","):
        if not item.strip():
            continue
        try:
            key_id, encoded = item.strip().split(":", 1)
            key = base64.b64decode(encoded, validate=True)
        except (ValueError, UnicodeError) as exc:
            raise HTTPException(503, "Неверная настройка ключей шифрования") from exc
        if len(key) != 32 or not key_id or key_id in result:
            raise HTTPException(503, "Неверная настройка ключей шифрования")
        result[key_id] = key
    return result


def encryption_ready() -> bool:
    try:
        return get_settings().broker_token_key_id in _keys()
    except HTTPException:
        return False


def environment_base(environment: str) -> str:
    if environment not in ("production", "sandbox"):
        raise HTTPException(422, "Выберите рабочий контур или песочницу")
    return SANDBOX_BASE if environment == "sandbox" else BASE


def token_aad(user_id: int, environment: str) -> bytes:
    environment_base(environment)
    return f"t_invest:{user_id}:1{':sandbox' if environment == 'sandbox' else ''}".encode()


def encrypt_token(token: str, user_id: int, environment: str = "production") -> tuple[str, str, str]:
    key_id = get_settings().broker_token_key_id
    key = _keys().get(key_id)
    if key is None:
        raise HTTPException(503, "Шифрование токена не настроено")
    nonce = os.urandom(12)
    aad = token_aad(user_id, environment)
    ciphertext = AESGCM(key).encrypt(nonce, token.encode(), aad)
    return base64.b64encode(ciphertext).decode(), base64.b64encode(nonce).decode(), key_id


def decrypt_token(connection: BrokerConnection) -> str:
    key = _keys().get(connection.token_key_id)
    if key is None:
        raise HTTPException(503, "Ключ подключения недоступен")
    try:
        nonce = base64.b64decode(connection.token_nonce, validate=True)
        ciphertext = base64.b64decode(connection.token_ciphertext, validate=True)
        aad = token_aad(connection.user_id, connection.environment)
        return AESGCM(key).decrypt(nonce, ciphertext, aad).decode()
    except Exception as exc:
        raise HTTPException(503, "Не удалось прочитать токен подключения") from exc


def decimal_value(value: dict | None) -> str | None:
    if not isinstance(value, dict):
        return None
    try:
        number = Decimal(str(value.get("units", "0"))) + Decimal(str(value.get("nano", 0))) / Decimal(10**9)
        return format(number, "f")
    except (InvalidOperation, TypeError, ValueError):
        return None


def money(value: dict | None) -> dict | None:
    amount = decimal_value(value)
    return {"amount": amount, "currency": value.get("currency", "")} if amount is not None else None


class TInvestClient:
    def __init__(self, token: str, transport: httpx.AsyncBaseTransport | None = None, *, environment: str = "production"):
        self.token = token
        self.transport = transport
        self.environment = environment
        self.base_url = environment_base(environment)

    async def _call(self, service: str, method: str, payload: dict) -> dict:
        url = f"{self.base_url}.{service}/{method}"
        headers = {"Authorization": f"Bearer {self.token}"}
        async with httpx.AsyncClient(timeout=15.0, transport=self.transport, verify=tls_context()) as client:
            for attempt in range(3):
                try:
                    response = await client.post(url, json=payload, headers=headers)
                except httpx.HTTPError as exc:
                    if attempt == 2:
                        raise HTTPException(502, "Т-Инвестиции временно недоступны") from exc
                    await asyncio.sleep(0.2 * (attempt + 1))
                    continue
                if response.status_code in (401, 403):
                    raise HTTPException(422, "Токен Т-Инвестиций недействителен или доступ закрыт")
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt == 2:
                        raise HTTPException(503, "Т-Инвестиции временно недоступны")
                    await asyncio.sleep(0.2 * (attempt + 1))
                    continue
                if response.is_error:
                    raise HTTPException(502, "Т-Инвестиции отклонили запрос")
                try:
                    data = response.json()
                except ValueError as exc:
                    raise HTTPException(502, "Некорректный ответ Т-Инвестиций") from exc
                if not isinstance(data, dict):
                    raise HTTPException(502, "Некорректный ответ Т-Инвестиций")
                return data
        raise HTTPException(503, "Т-Инвестиции временно недоступны")

    async def accounts(self) -> list[dict]:
        service, method = ("SandboxService", "GetSandboxAccounts") if self.environment == "sandbox" else ("UsersService", "GetAccounts")
        data = await self._call(service, method, {"status": "ACCOUNT_STATUS_OPEN"})
        rows = data.get("accounts")
        if not isinstance(rows, list):
            raise HTTPException(502, "Нет списка счетов в ответе брокера")
        if self.environment == "production" and any(row.get("accessLevel") != READ_ONLY for row in rows):
            raise HTTPException(422, "Нужен токен только для чтения для всех доступных счетов")
        return [row for row in rows if row.get("id") and row.get("type") != "ACCOUNT_TYPE_INVEST_BOX"]

    async def portfolio(self, account_id: str) -> tuple[dict, dict]:
        service = "SandboxService" if self.environment == "sandbox" else "OperationsService"
        prefix = "GetSandbox" if self.environment == "sandbox" else "Get"
        portfolio, positions = await asyncio.gather(
            self._call(service, prefix + "Portfolio", {"accountId": account_id}),
            self._call(service, prefix + "Positions", {"accountId": account_id}),
        )
        if not isinstance(portfolio.get("positions"), list) or not isinstance(positions.get("securities", []), list):
            raise HTTPException(502, "Неполный ответ брокера")
        return portfolio, positions

    async def instrument(self, uid: str) -> dict | None:
        try:
            data = await self._call("InstrumentsService", "GetInstrumentBy", {"idType": "INSTRUMENT_ID_TYPE_UID", "id": uid})
        except HTTPException:
            return None
        return data.get("instrument") if isinstance(data.get("instrument"), dict) else None

    async def trading_calendar(self, ticker: str, is_future: bool, start: datetime, stop: datetime) -> tuple[dict, dict]:
        data = await self._call("InstrumentsService", "GetInstrumentBy", {
            "idType": "INSTRUMENT_ID_TYPE_TICKER", "classCode": "SPBFUT" if is_future else "TQBR", "id": ticker,
        })
        instrument = data.get("instrument") or {}
        if instrument.get("realExchange") != "REAL_EXCHANGE_MOEX" or not instrument.get("exchange") or instrument.get("ticker") != ticker:
            raise HTTPException(502, "Площадка инструмента MOEX не подтверждена Т-Инвестициями")
        calendar = await self._call("InstrumentsService", "TradingSchedules", {
            "exchange": instrument["exchange"],
            "from": datetime.combine(start.date(), datetime.min.time(), timezone.utc).isoformat(),
            "to": (datetime.combine(stop.date() + timedelta(days=1), datetime.min.time(), timezone.utc) - timedelta(microseconds=1)).isoformat(),
        })
        return instrument, calendar

    async def sandbox_positions(self, account_id: str) -> dict:
        if self.environment != "sandbox":
            raise HTTPException(409, "Выберите подключение песочницы")
        return await self._call("SandboxService", "GetSandboxPositions", {"accountId": account_id})

    async def sandbox_change(self, method: str, payload: dict) -> dict:
        if self.environment != "sandbox" or method not in ("OpenSandboxAccount", "SandboxPayIn", "CloseSandboxAccount"):
            raise HTTPException(409, "Операция доступна только в песочнице")
        try:
            async with httpx.AsyncClient(timeout=15, transport=self.transport, verify=tls_context()) as client:
                response = await client.post(f"{SANDBOX_BASE}.SandboxService/{method}", json=payload, headers={"Authorization": f"Bearer {self.token}"})
        except httpx.HTTPError as exc:
            raise SandboxUnknown() from exc
        if response.status_code >= 500 or response.status_code == 408:
            raise SandboxUnknown()
        if response.is_error:
            raise HTTPException(422, "Брокер отклонил действие. Проверьте токен, счёт и сумму")
        try:
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except ValueError as exc:
            raise SandboxUnknown() from exc


class SandboxUnknown(Exception):
    pass


async def connection_for(session: AsyncSession, user_id: int, *, environment: str | None = None, lock=False) -> BrokerConnection | None:
    query = select(BrokerConnection).where(BrokerConnection.user_id == user_id, BrokerConnection.provider == "t_invest")
    if environment is not None:
        environment_base(environment)
        query = query.where(BrokerConnection.environment == environment)
    query = query.order_by(BrokerConnection.environment).limit(1)
    return await session.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)


async def ensure_available(session, connection):
    if connection.status == "sandbox_busy":
        now = datetime.now(timezone.utc)
        pending = (await session.scalars(select(BrokerSandboxAction).where(BrokerSandboxAction.connection_id == connection.id, BrokerSandboxAction.status == "pending"))).all()
        if any((now - row.started_at.replace(tzinfo=timezone.utc)).total_seconds() < 120 for row in pending):
            raise HTTPException(409, "Действие песочницы ещё выполняется")
        for row in pending:
            row.status, row.finished_at, row.error_code = "unknown", now, "interrupted"
        connection.status = "connected"
    if connection.status == "refreshing":
        raise HTTPException(409, "Обновление уже выполняется")


def _event(session: AsyncSession, user_id: int, connection_id: int | None, action: str, result: str, code: str | None = None) -> None:
    session.add(BrokerConnectionEvent(user_id=user_id, connection_id=connection_id, action=action, result=result, error_code=code))


async def connect(session: AsyncSession, user_id: int, token: str, client: TInvestClient | None = None, *, environment: str = "production") -> BrokerConnection:
    environment_base(environment)
    token = token.strip()
    if not token or len(token) > 4096:
        raise HTTPException(422, "Укажите токен Т-Инвестиций")
    ciphertext, nonce, key_id = encrypt_token(token, user_id, environment)
    rows = await (client or TInvestClient(token, environment=environment)).accounts()
    if not rows and environment == "production":
        raise HTTPException(422, "Нет доступных брокерских счетов")
    connection = await connection_for(session, user_id, environment=environment, lock=True)
    if connection is None:
        connection = BrokerConnection(user_id=user_id, provider="t_invest", environment=environment, token_ciphertext=ciphertext, token_nonce=nonce, token_key_id=key_id)
        session.add(connection)
        await session.flush()
    else:
        await ensure_available(session, connection)
        connection.environment = environment
        connection.token_ciphertext = ciphertext
        connection.token_nonce = nonce
        connection.token_key_id = key_id
        connection.version += 1
        connection.status = "connected"
        connection.last_error = None
    existing = {a.external_id: a for a in (await session.scalars(select(BrokerAccount).where(BrokerAccount.connection_id == connection.id))).all()}
    for row in rows:
        account = existing.pop(row["id"], None)
        if account is None:
            account = BrokerAccount(connection_id=connection.id, external_id=row["id"])
            session.add(account)
        account.name = row.get("name") or "Счёт Т-Инвестиций"
        account.account_type = row.get("type") or ""
        account.status = row.get("status") or ""
        account.access_level = row.get("accessLevel") or ""
    for account in existing.values():
        if connection.selected_account_id == account.external_id:
            connection.selected_account_id = None
        await session.execute(delete(BrokerPortfolioSnapshot).where(BrokerPortfolioSnapshot.account_id == account.id))
        await session.delete(account)
    _event(session, user_id, connection.id, "connect", "ok")
    await session.commit()
    return connection


async def _snapshot(session: AsyncSession, client: TInvestClient, account: BrokerAccount) -> dict:
    portfolio, positions = await client.portfolio(account.external_id)
    blocked_by_uid = {item.get("instrumentUid") or item.get("figi"): item for item in positions.get("securities", [])}
    items = []
    for row in portfolio["positions"]:
        uid = row.get("instrumentUid") or row.get("figi") or ""
        instrument = await client.instrument(uid) if uid else None
        ticker = instrument.get("ticker") if instrument else None
        class_code = instrument.get("classCode") if instrument else None
        local_ticker = None
        if ticker and class_code == "TQBR" and row.get("instrumentType") == "share":
            matches = (await session.scalars(select(Security).where(Security.ticker == ticker, Security.security_type == "stock", Security.market == "MOEX"))).all()
            if len(matches) == 1:
                local_ticker = ticker
        blocked = blocked_by_uid.get(uid) or {}
        items.append({
            "uid": uid,
            "name": instrument.get("name") if instrument else uid,
            "ticker": ticker,
            "local_ticker": local_ticker,
            "type": row.get("instrumentType"),
            "quantity": decimal_value(row.get("quantity")),
            "quantity_lots": decimal_value(row.get("quantityLots")),
            "average_price": money(row.get("averagePositionPrice")),
            "current_price": money(row.get("currentPrice")),
            "expected_yield": decimal_value(row.get("expectedYield")),
            "blocked": row.get("blocked") or blocked.get("blocked", False),
        })
    cash = [money(item) for item in positions.get("money", []) if money(item) is not None]
    return {
        "total": money(portfolio.get("totalAmountPortfolio")),
        "expected_yield": decimal_value(portfolio.get("expectedYield")),
        "cash": cash,
        "positions": items,
    }


async def refresh(session: AsyncSession, user_id: int, account_id: str | None = None, client: TInvestClient | None = None, *, environment: str | None = None) -> dict:
    connection = await connection_for(session, user_id, environment=environment, lock=True)
    if connection is None:
        raise HTTPException(404, "Подключение не найдено")
    await ensure_available(session, connection)
    target = account_id or connection.selected_account_id
    account = await session.scalar(select(BrokerAccount).where(BrokerAccount.connection_id == connection.id, BrokerAccount.external_id == target))
    if account is None:
        raise HTTPException(404, "Счёт недоступен")
    broker = client or TInvestClient(decrypt_token(connection), environment=connection.environment)
    version = connection.version
    started = await session.execute(update(BrokerConnection).where(BrokerConnection.id == connection.id, BrokerConnection.version == version, BrokerConnection.status != "refreshing").values(status="refreshing"))
    if not started.rowcount:
        await session.rollback()
        raise HTTPException(409, "Обновление уже выполняется")
    await session.commit()
    try:
        rows = await broker.accounts()
        if not any(row.get("id") == target for row in rows):
            raise HTTPException(422, "Счёт больше недоступен")
        data = await _snapshot(session, broker, account)
    except HTTPException as exc:
        await session.rollback()
        changed = await session.execute(update(BrokerConnection).where(BrokerConnection.id == connection.id, BrokerConnection.version == version, BrokerConnection.status == "refreshing").values(status="error", last_error="broker_unavailable"))
        if changed.rowcount:
            _event(session, user_id, connection.id, "refresh", "error", "broker_unavailable")
            await session.commit()
        raise exc
    changed = await session.execute(update(BrokerConnection).where(BrokerConnection.id == connection.id, BrokerConnection.version == version, BrokerConnection.status == "refreshing").values(status="connected", selected_account_id=target, last_error=None, last_refreshed_at=datetime.now(timezone.utc), version=version + 1))
    if not changed.rowcount:
        await session.rollback()
        raise HTTPException(409, "Подключение изменилось во время обновления")
    snapshot = await session.scalar(select(BrokerPortfolioSnapshot).where(BrokerPortfolioSnapshot.account_id == account.id))
    if snapshot is None:
        snapshot = BrokerPortfolioSnapshot(account_id=account.id, fetched_at=datetime.now(timezone.utc), data=data)
        session.add(snapshot)
    else:
        snapshot.fetched_at = datetime.now(timezone.utc)
        snapshot.data = data
    _event(session, user_id, connection.id, "refresh", "ok")
    await session.commit()
    return data


async def disconnect(session: AsyncSession, user_id: int, *, environment: str | None = None) -> None:
    connection = await connection_for(session, user_id, environment=environment, lock=True)
    if connection is None:
        raise HTTPException(404, "Подключение не найдено")
    await ensure_available(session, connection)
    connection_id = connection.id
    accounts = (await session.scalars(select(BrokerAccount).where(BrokerAccount.connection_id == connection_id))).all()
    for account in accounts:
        await session.execute(delete(BrokerPortfolioSnapshot).where(BrokerPortfolioSnapshot.account_id == account.id))
        await session.delete(account)
    await session.delete(connection)
    _event(session, user_id, connection_id, "disconnect", "ok")
    await session.commit()


async def view(session: AsyncSession, user_id: int, *, environment: str | None = None) -> dict:
    connection = await connection_for(session, user_id, environment=environment)
    if connection is None:
        return {"connected": False, "environment": environment or "production", "accounts": [], "selected_account_id": None, "snapshot": None}
    accounts = (await session.scalars(select(BrokerAccount).where(BrokerAccount.connection_id == connection.id).order_by(BrokerAccount.id))).all()
    account = next((row for row in accounts if row.external_id == connection.selected_account_id), None)
    snapshots = {row.account_id: row for row in (await session.scalars(select(BrokerPortfolioSnapshot).join(BrokerAccount).where(BrokerAccount.connection_id == connection.id))).all()}
    snapshot = snapshots.get(account.id) if account else None
    return {
        "connected": True,
        "environment": connection.environment,
        "status": connection.status,
        "last_error": connection.last_error,
        "last_refreshed_at": connection.last_refreshed_at,
        "selected_account_id": connection.selected_account_id,
        "accounts": [{"id": a.external_id, "masked_id": "••••" + a.external_id[-4:], "name": a.name, "type": a.account_type, "status": a.status, "balance": snapshots[a.id].data.get("cash", []) if a.id in snapshots else None, "balance_fetched_at": snapshots[a.id].fetched_at if a.id in snapshots else None} for a in accounts],
        "snapshot": {"fetched_at": snapshot.fetched_at, **snapshot.data} if snapshot else None,
    }
