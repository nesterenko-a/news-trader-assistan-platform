import base64
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.brokers import sandbox, t_invest
from app.config import Settings
from app.db.connection import Base, get_session
from app.db.models import BrokerAccount, BrokerPortfolioSnapshot, BrokerSandboxAction, User


class Broker:
    def __init__(self):
        self.rows = []
        self.calls = []
        self.balance = "0"
        self.failure = None
        self.sync_failure = False

    async def accounts(self):
        if self.sync_failure and self.calls:
            raise HTTPException(503, "unavailable")
        return list(self.rows)

    async def sandbox_positions(self, account_id):
        return {"money": [{"currency": "rub", "units": self.balance, "nano": 0}]}

    async def sandbox_change(self, method, payload):
        self.calls.append((method, payload))
        if self.failure:
            raise self.failure
        if method == "OpenSandboxAccount":
            account_id = str(uuid4())
            self.rows.append({"id": account_id, "name": payload.get("name", "Тестовый"), "status": "ACCOUNT_STATUS_OPEN"})
            return {"accountId": account_id}
        if method == "SandboxPayIn":
            self.balance = payload["amount"]["units"]
            return {"balance": payload["amount"]}
        self.rows = [row for row in self.rows if row["id"] != payload["accountId"]]
        return {}


@pytest_asyncio.fixture
async def store(monkeypatch):
    settings = Settings(_env_file=None, broker_token_keys="v1:" + base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setattr(t_invest, "get_settings", lambda: settings)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        user = User(username="sandbox-owner", password_hash="x")
        session.add(user)
        await session.commit()
        broker = Broker()
        await t_invest.connect(session, user.id, "test-only", broker, environment="sandbox")
        yield session, user, broker
    await engine.dispose()


async def test_create_pay_close_sync_and_duplicate(store):
    session, user, broker = store
    key = str(uuid4())
    created = await sandbox.execute(session, user.id, "create", key, name="Учебный", client=broker)
    assert created["status"] == "success"
    assert (await sandbox.execute(session, user.id, "create", key, name="Учебный", client=broker))["id"] == created["id"]
    assert len(broker.calls) == 1
    with pytest.raises(HTTPException) as conflict:
        await sandbox.execute(session, user.id, "create", key, name="Другой", client=broker)
    assert conflict.value.status_code == 409
    account = await session.scalar(select(BrokerAccount))
    connection = await t_invest.connection_for(session, user.id)
    connection.selected_account_id = account.external_id
    session.add(BrokerPortfolioSnapshot(account_id=account.id, fetched_at=datetime.now(timezone.utc), data={"positions": []}))
    await session.commit()
    paid = await sandbox.execute(session, user.id, "pay_in", str(uuid4()), account_id=account.external_id, amount="1234,56", client=broker)
    assert paid["status"] == "success"
    assert broker.calls[-1][1]["amount"] == {"currency": "rub", "units": "1234", "nano": 560000000}
    assert await session.scalar(select(BrokerPortfolioSnapshot)) is None
    assert (await sandbox.list_accounts(session, user.id, broker))["accounts"][0]["rub_balance"] == "1234"
    with pytest.raises(HTTPException):
        await sandbox.execute(session, user.id, "close", str(uuid4()), account_id=account.external_id, client=broker)
    await sandbox.execute(session, user.id, "close", str(uuid4()), account_id=account.external_id, confirmed=True, client=broker)
    assert connection.selected_account_id is None
    assert await session.scalar(select(BrokerAccount)) is None
    assert broker.rows == []


@pytest.mark.parametrize("amount", ["0", "-1", "NaN", "Infinity", "1e6", "12.345", "", "9" * 30])
def test_invalid_amount(amount):
    with pytest.raises(HTTPException):
        sandbox.amount_value(amount)


async def test_unknown_and_failed_are_persisted_without_repeat(store):
    session, user, broker = store
    for failure, status in [(t_invest.SandboxUnknown(), "unknown"), (HTTPException(422, "reject"), "failed")]:
        key = str(uuid4())
        broker.failure = failure
        result = await sandbox.execute(session, user.id, "create", key, client=broker)
        assert result["status"] == status
        count = len(broker.calls)
        assert (await sandbox.execute(session, user.id, "create", key, client=broker))["status"] == status
        assert len(broker.calls) == count


async def test_sync_failure_does_not_repeat_success(store):
    session, user, broker = store
    broker.sync_failure = True
    key = str(uuid4())
    result = await sandbox.execute(session, user.id, "create", key, client=broker)
    assert result["status"] == "success" and result["error_code"] == "sync_failed"
    await sandbox.execute(session, user.id, "create", key, client=broker)
    assert len(broker.calls) == 1


async def test_production_other_owner_and_foreign_account_are_blocked(store):
    session, user, broker = store
    with pytest.raises(HTTPException) as missing:
        await sandbox.execute(session, user.id + 1, "create", str(uuid4()), client=broker)
    assert missing.value.status_code == 409
    with pytest.raises(HTTPException) as foreign:
        await sandbox.execute(session, user.id, "pay_in", str(uuid4()), account_id="foreign", amount="100", client=broker)
    assert foreign.value.status_code == 404
    connection = await t_invest.connection_for(session, user.id)
    connection.environment = "production"
    await session.commit()
    with pytest.raises(HTTPException) as production:
        await sandbox.execute(session, user.id, "create", str(uuid4()), client=broker)
    assert production.value.status_code == 409 and not broker.calls


async def test_pending_blocks_changes_and_recovers_after_lease(store):
    session, user, broker = store
    connection = await t_invest.connection_for(session, user.id)
    connection.status = "sandbox_busy"
    action = BrokerSandboxAction(id=str(uuid4()), user_id=user.id, connection_id=connection.id, connection_version=connection.version, operation_key=str(uuid4()), kind="create", request_hash="x", payload={}, result={}, status="pending", started_at=datetime.now(timezone.utc))
    session.add(action)
    await session.commit()
    for operation in (lambda: t_invest.disconnect(session, user.id), lambda: t_invest.connect(session, user.id, "new", broker, environment="sandbox"), lambda: t_invest.refresh(session, user.id, "missing", broker)):
        with pytest.raises(HTTPException) as busy:
            await operation()
        assert busy.value.status_code == 409
    action.started_at = datetime.now(timezone.utc) - timedelta(minutes=3)
    await session.commit()
    await sandbox.sync(session, user.id, broker)
    assert action.status == "unknown" and connection.status == "connected"


@pytest.mark.parametrize("failure", ["timeout", "server", "bad_json"])
async def test_changing_http_request_never_retries(failure):
    calls = []
    def handler(request):
        assert request.url.host == "sandbox-invest-public-api.tbank.ru"
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("timeout")
        return httpx.Response(500 if failure == "server" else 200, text="invalid")
    client = t_invest.TInvestClient("test", httpx.MockTransport(handler), environment="sandbox")
    with pytest.raises(t_invest.SandboxUnknown):
        await client.sandbox_change("OpenSandboxAccount", {})
    assert len(calls) == 1


async def test_api_csrf_validation_and_actions(store, monkeypatch):
    from fastapi import FastAPI
    from app.api.routes.brokers import router
    from app.auth import get_current_user
    session, user, broker = store
    monkeypatch.setattr(sandbox, "broker_client", lambda row, client: broker)
    app = FastAPI()
    app.include_router(router, prefix="/v1")
    async def db():
        yield session
    app.dependency_overrides[get_session] = db
    app.dependency_overrides[get_current_user] = lambda: user
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test", cookies={"nt_token": "test"}) as client:
        payload = {"operation_key": str(uuid4()), "name": "API"}
        url = "/v1/brokers/t-invest/sandbox/accounts"
        assert (await client.post(url, json=payload)).status_code == 403
        response = await client.post(url, json=payload, headers={"Origin": "http://test"})
        assert response.status_code == 200 and response.json()["status"] == "success"
        assert response.headers["cache-control"] == "no-store"
        assert len((await client.get(url)).json()["accounts"]) == 1
