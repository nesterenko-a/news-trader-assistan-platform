import base64
import os
from importlib import import_module

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.brokers import t_invest
from app.config import Settings
from app.db.connection import Base, get_session
from app.db.models import User


class Broker:
    def __init__(self, environment):
        self.environment = environment
        self.failure = None

    async def accounts(self):
        if self.failure:
            raise HTTPException(self.failure, "private-diagnostic-must-not-leak")
        return [{"id": key, "name": "Счёт " + key, "status": "ACCOUNT_STATUS_OPEN", "accessLevel": t_invest.READ_ONLY} for key in ("one", "two")]

    async def portfolio(self, account_id):
        amount = "10" if self.environment == "production" else "20"
        cash = [] if account_id == "two" else [{"units": amount, "nano": 0, "currency": "rub"}]
        return {"positions": [], "totalAmountPortfolio": {"units": amount, "nano": 0, "currency": "rub"}}, {"money": cash, "securities": []}


@pytest_asyncio.fixture
async def inline_app(monkeypatch):
    settings = Settings(_env_file=None, broker_token_keys="v1:" + base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setattr(t_invest, "get_settings", lambda: settings)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        owner = User(username="inline-owner", password_hash="x")
        other = User(username="inline-other", password_hash="x")
        session.add_all([owner, other])
        await session.commit()
        brokers = {environment: Broker(environment) for environment in ("production", "sandbox")}
        for environment, broker in brokers.items():
            await t_invest.connect(session, owner.id, "test-token", broker, environment=environment)
        monkeypatch.setattr(t_invest, "TInvestClient", lambda token, *, environment: brokers[environment])
        current = [owner]
        web = import_module("app.web.router")

        async def user(*args):
            return current[0]

        async def db():
            yield session

        monkeypatch.setattr(web, "_optional_user", user)
        app = FastAPI()
        app.include_router(web.router)
        app.dependency_overrides[get_session] = db
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            yield client, session, current, other, brokers
    await engine.dispose()


HEADERS = {"Origin": "http://test", "X-Requested-With": "XMLHttpRequest"}


@pytest.mark.parametrize("environment", ["production", "sandbox"])
async def test_select_refresh_fragment_and_other_environment_preserved(inline_app, environment):
    client, session, current, _, _ = inline_app
    response = await client.post("/broker/t-invest/select", data={"environment": environment, "account_id": "one"}, headers=HEADERS)
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    assert f'id="broker-content-{environment}"' in response.text
    assert 'data-selected-account="one"' in response.text
    assert "test-token" not in response.text and "<!doctype" not in response.text
    assert "<h3>Деньги</h3>" in response.text
    response = await client.post("/broker/t-invest/refresh", data={"environment": environment}, headers=HEADERS)
    assert response.status_code == 200 and "Получено:" in response.text
    other_environment = "sandbox" if environment == "production" else "production"
    assert (await t_invest.view(session, current[0].id, environment=other_environment))["snapshot"] is None
    response = await client.post("/broker/t-invest/select", data={"environment": environment, "account_id": "two"}, headers=HEADERS)
    assert response.status_code == 200 and "Нет денежных позиций" in response.text
    assert 'data-selected-account="two"' in response.text


@pytest.mark.parametrize("code", [409, 422, 502, 503])
async def test_error_retains_snapshot_and_hides_private_details(inline_app, code):
    client, session, current, _, brokers = inline_app
    await client.post("/broker/t-invest/select", data={"environment": "production", "account_id": "one"}, headers=HEADERS)
    previous = (await t_invest.view(session, current[0].id, environment="production"))["snapshot"]
    brokers["production"].failure = code
    response = await client.post("/broker/t-invest/select", data={"environment": "production", "account_id": "two"}, headers=HEADERS)
    assert response.status_code == code and response.headers["cache-control"] == "no-store"
    assert "private-diagnostic" not in response.text and response.json()["detail"]
    current_view = await t_invest.view(session, current[0].id, environment="production")
    assert current_view["snapshot"] == previous and current_view["selected_account_id"] == "one"


async def test_auth_origin_validation_and_classic_form_redirect(inline_app):
    client, _, current, other, _ = inline_app
    payload = {"environment": "sandbox", "account_id": "one"}
    response = await client.post("/broker/t-invest/select", data=payload, headers={"X-Requested-With": "XMLHttpRequest"})
    assert response.status_code == 403 and response.json()["detail"]
    for invalid in ({"account_id": "one"}, {"environment": "invalid", "account_id": "one"}):
        response = await client.post("/broker/t-invest/select", data=invalid, headers=HEADERS)
        assert response.status_code == 422
    response = await client.post("/broker/t-invest/select", data=payload, headers={"Origin": "http://test"})
    assert response.status_code == 303 and "environment=sandbox#broker-portfolio" in response.headers["location"]
    response = await client.post("/broker/t-invest/refresh", data={"environment": "sandbox"}, headers={"Origin": "http://test"})
    assert response.status_code == 303
    current[0] = other
    assert (await client.post("/broker/t-invest/select", data=payload, headers=HEADERS)).status_code == 404
    current[0] = None
    response = await client.post("/broker/t-invest/refresh", data={"environment": "sandbox"}, headers=HEADERS)
    assert response.status_code == 401 and response.headers["content-type"].startswith("application/json")
    assert (await client.post("/broker/t-invest/refresh", data={}, headers={"Origin": "http://test"})).headers["location"] == "/login"
