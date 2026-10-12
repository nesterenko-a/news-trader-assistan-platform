import base64
import os

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.brokers import t_invest
from app.config import Settings
from app.db.connection import Base
from app.db.models import BrokerConnection, BrokerPortfolioSnapshot, User


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
        data = await t_invest.refresh(session, owner.id, "12345678", FakeBroker())
        assert data["total"] == {"amount": "100.5", "currency": "rub"}
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
