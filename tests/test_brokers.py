import base64
import io
import os
import ssl
from importlib import import_module
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.brokers import certificates, t_invest
from app.config import Settings
from app.db.connection import Base
from app.db.models import BrokerConnection, BrokerPortfolioSnapshot, User


def test_t_invest_tls_context_trusts_ministry_root():
    context = certificates.tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    assert any(
        subject_part == (("commonName", "Russian Trusted Root CA"),)
        for certificate in context.get_ca_certs()
        for subject_part in certificate["subject"]
    )


def test_install_t_invest_certificate_is_scoped_and_pinned(monkeypatch, tmp_path):
    local_ca = tmp_path / "certs" / "root.pem"
    monkeypatch.setattr(certificates, "LOCAL_CA", local_ca)
    original = certificates.ROOT_CA.read_bytes()
    monkeypatch.setattr(certificates.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(original))
    assert certificates.install_certificate() == local_ca
    assert local_ca.read_bytes() == original
    assert certificates.tls_context().check_hostname
    monkeypatch.setattr(certificates.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(b"invalid"))
    with pytest.raises(ValueError):
        certificates.install_certificate()
    assert local_ca.read_bytes() == original


@pytest.mark.asyncio
async def test_broker_certificate_repair_available_to_regular_user(monkeypatch):
    web_router = import_module("app.web.router")

    async def regular_user(*args):
        return SimpleNamespace(id=1, role="user")

    monkeypatch.setattr(web_router, "_optional_user", regular_user)
    monkeypatch.setattr(web_router.broker_certificates, "repair_and_check", lambda base: certificates.LOCAL_CA)
    request = Request({"type": "http", "method": "POST", "path": "/broker/t-invest/repair-certificate", "headers": [(b"host", b"localhost"), (b"origin", b"http://localhost")]})
    response = await web_router.broker_repair_certificate(request, None)
    assert response.status_code == 303
    assert "certificate=ok" in response.headers["location"]


class FakeBroker:
    async def accounts(self):
        return [{"id": "12345678", "name": "Основной", "type": "ACCOUNT_TYPE_TINKOFF", "status": "ACCOUNT_STATUS_OPEN", "accessLevel": t_invest.READ_ONLY}]

    async def portfolio(self, account_id):
        assert account_id == "12345678"
        return (
            {"totalAmountPortfolio": {"units": "100", "nano": 500000000, "currency": "rub"}, "positions": [{"instrumentUid": "instrument-1", "quantity": {"units": "2", "nano": 0}, "instrumentType": "share"}]},
            {"money": [{"units": "10", "nano": 0, "currency": "rub"}], "securities": []},
        )

    async def instrument(self, uid):
        return {"ticker": "SBER", "name": "Сбербанк", "classCode": "TQBR"}


@pytest.mark.asyncio
async def test_broker_connection_roundtrip_and_isolation(monkeypatch):
    key = base64.b64encode(os.urandom(32)).decode()
    settings = Settings(_env_file=None, broker_token_keys=f"v1:{key}")
    monkeypatch.setattr(t_invest, "get_settings", lambda: settings)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all([User(username="broker-a", password_hash="x"), User(username="broker-b", password_hash="x")])
        await session.commit()
        users = (await session.scalars(select(User).where(User.username.in_(["broker-a", "broker-b"])).order_by(User.id))).all()
        owner, other = users
        await t_invest.connect(session, owner.id, "secret-token", FakeBroker())
        stored = await t_invest.connection_for(session, owner.id)
        assert "secret-token" not in stored.token_ciphertext
        assert t_invest.decrypt_token(stored) == "secret-token"
        assert not (await t_invest.view(session, other.id))["connected"]
        with pytest.raises(HTTPException) as denied:
            await t_invest.refresh(session, other.id, "12345678", FakeBroker())
        assert denied.value.status_code == 404
        assert (await t_invest.view(session, owner.id))["accounts"][0]["balance"] is None
        data = await t_invest.refresh(session, owner.id, "12345678", FakeBroker())
        assert data["total"] == {"amount": "100.5", "currency": "rub"}
        account_view = (await t_invest.view(session, owner.id))["accounts"][0]
        assert account_view["balance"] == [{"amount": "10", "currency": "rub"}]
        assert account_view["balance_fetched_at"] is not None
        assert (await t_invest.view(session, owner.id))["snapshot"]["positions"][0]["quantity"] == "2"
        stored = await t_invest.connection_for(session, owner.id)
        stored.user_id = other.id
        with pytest.raises(HTTPException):
            t_invest.decrypt_token(stored)
        stored.user_id = owner.id
        await session.commit()
        await t_invest.disconnect(session, owner.id)
        assert await t_invest.connection_for(session, owner.id) is None
        assert (await session.scalars(select(BrokerPortfolioSnapshot))).all() == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_broker_rejects_full_access_and_bad_response():
    import httpx

    def handler(request):
        return httpx.Response(200, json={"accounts": [{"id": "1", "accessLevel": "ACCOUNT_ACCESS_LEVEL_FULL_ACCESS"}]})

    client = t_invest.TInvestClient("test", httpx.MockTransport(handler))
    with pytest.raises(HTTPException) as error:
        await client.accounts()
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_sandbox_routes_accounts_portfolio_and_instruments():
    import httpx

    calls = []
    def handler(request):
        assert request.url.host == "sandbox-invest-public-api.tbank.ru"
        calls.append(request.url.path)
        if request.url.path.endswith("GetSandboxAccounts"):
            return httpx.Response(200, json={"accounts": [{"id": "sandbox-1", "accessLevel": "ACCOUNT_ACCESS_LEVEL_FULL_ACCESS"}]})
        if request.url.path.endswith("GetSandboxPortfolio"):
            return httpx.Response(200, json={"positions": []})
        if request.url.path.endswith("GetSandboxPositions"):
            return httpx.Response(200, json={"securities": []})
        return httpx.Response(200, json={"instrument": {"ticker": "SBER"}})

    client = t_invest.TInvestClient("test", httpx.MockTransport(handler), environment="sandbox")
    assert (await client.accounts())[0]["id"] == "sandbox-1"
    assert (await client.portfolio("sandbox-1"))[0]["positions"] == []
    assert (await client.instrument("uid"))["ticker"] == "SBER"
    assert all("SandboxService/GetSandbox" in path or "InstrumentsService/GetInstrumentBy" in path for path in calls)
    with pytest.raises(HTTPException):
        t_invest.TInvestClient("test", environment="https://other.test")


@pytest.mark.asyncio
async def test_sandbox_connection_empty_accounts_and_environment_switch(monkeypatch):
    import httpx
    settings = Settings(_env_file=None, broker_token_keys="v1:" + base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setattr(t_invest, "get_settings", lambda: settings)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        user = User(username="sandbox", password_hash="x")
        session.add(user)
        await session.commit()
        await t_invest.connect(session, user.id, "production-test", FakeBroker())
        await t_invest.refresh(session, user.id, "12345678", FakeBroker())
        assert (await t_invest.view(session, user.id))["snapshot"]
        empty = t_invest.TInvestClient("sandbox-test", httpx.MockTransport(lambda request: httpx.Response(200, json={"accounts": []})), environment="sandbox")
        stored = await t_invest.connect(session, user.id, "sandbox-test", empty, environment="sandbox")
        data = await t_invest.view(session, user.id)
        assert data["connected"] and data["environment"] == "sandbox"
        assert data["accounts"] == [] and data["snapshot"] is None
        assert data["selected_account_id"] is None
        assert stored.last_refreshed_at is None
        assert t_invest.decrypt_token(stored) == "sandbox-test"
        stored.environment = "production"
        with pytest.raises(HTTPException):
            t_invest.decrypt_token(stored)
        stored.environment = "sandbox"
        with pytest.raises(HTTPException):
            await t_invest.connect(session, user.id, "empty-production", empty)
        assert (await t_invest.view(session, user.id))["environment"] == "sandbox"
        await t_invest.connect(session, user.id, "production-test", FakeBroker())
        assert t_invest.decrypt_token(await t_invest.connection_for(session, user.id)) == "production-test"
    await engine.dispose()


@pytest.mark.asyncio
async def test_refresh_uses_saved_environment(monkeypatch):
    settings = Settings(_env_file=None, broker_token_keys="v1:" + base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setattr(t_invest, "get_settings", lambda: settings)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        user = User(username="saved-sandbox", password_hash="x")
        session.add(user)
        await session.commit()
        await t_invest.connect(session, user.id, "sandbox-test", FakeBroker(), environment="sandbox")
        def create_client(token, *, environment):
            assert token == "sandbox-test" and environment == "sandbox"
            return FakeBroker()
        monkeypatch.setattr(t_invest, "TInvestClient", create_client)
        assert (await t_invest.refresh(session, user.id, "12345678"))["total"]["amount"] == "100.5"
    await engine.dispose()


@pytest.mark.asyncio
async def test_broker_form_forwards_sandbox_and_keeps_it_on_error(monkeypatch):
    from starlette.datastructures import FormData
    web_router = import_module("app.web.router")
    async def regular_user(*args):
        return SimpleNamespace(id=1)
    async def form(request):
        return FormData({"token": "test", "environment": "sandbox"})
    async def connect(session, user_id, token, *, environment):
        assert user_id == 1 and token == "test" and environment == "sandbox"
        raise HTTPException(422, "test")
    monkeypatch.setattr(web_router, "_optional_user", regular_user)
    monkeypatch.setattr(Request, "form", form)
    monkeypatch.setattr(t_invest, "connect", connect)
    request = Request({"type": "http", "method": "POST", "path": "/broker/t-invest/connect", "headers": [(b"host", b"localhost"), (b"origin", b"http://localhost")]})
    response = await web_router.broker_connect(request, None)
    assert "environment=sandbox" in response.headers["location"]
    assert "token" not in response.headers["location"]


@pytest.mark.parametrize("environment,connected", [("production", True), ("sandbox", True), ("production", False)])
def test_embedded_broker_panels_gate_controls_by_connection(environment, connected):
    web_router = import_module("app.web.router")
    request = Request({"type": "http", "method": "GET", "path": "/settings/brokers", "headers": [], "query_string": b""})
    broker = {"connected": connected, "environment": environment, "accounts": [{"id": "1", "name": "Основной", "masked_id": "••••1"}], "selected_account_id": "1", "snapshot": None, "last_error": None}
    context = {"request": request, "broker": broker, "operation_key": "test-key"}
    portfolio = web_router.templates.env.get_template("broker_portfolio.html").render(context)
    sandbox = web_router.templates.env.get_template("broker_sandbox.html").render(context)
    assert ('action="/broker/t-invest/select"' in portfolio) == connected
    assert ('action="/broker/t-invest/refresh"' in portfolio) == connected
    assert ('id="sandbox-create"' in sandbox) == (connected and environment == "sandbox")
    assert ('src="/static/broker-sandbox.js"' in sandbox) == (connected and environment == "sandbox")
    assert ('broker-panel-disabled' in portfolio) == (not connected)
    assert ('broker-panel-disabled' in sandbox) == (not connected or environment != "sandbox")

    accounts = web_router.templates.env.get_template("broker_accounts.html").render(context)
    assert ('action="/broker/t-invest/select"' in accounts) == (connected and environment == "production")
    assert ('broker-panel-disabled' in accounts) == (not connected or environment != "production")
