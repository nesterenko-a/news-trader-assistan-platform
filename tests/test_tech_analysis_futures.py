"""Тесты on-demand создания фьючерсной бумаги для «Теханализ в LLM»."""

from unittest.mock import AsyncMock, patch

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.connection import Base
from app.db.models import Security
from app.tech_analysis.service import _load_security


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as store:
        yield store
    await engine.dispose()


async def test_load_existing_no_moex(session):
    session.add(Security(ticker="AFLT", name="Аэрофлот", market="MOEX",
                         security_type="stock", sector="", aliases=[]))
    await session.commit()
    with patch("app.tech_analysis.service.MOEXClient.fetch_futures_list",
               new=AsyncMock(side_effect=AssertionError("не должен вызываться MOEX"))):
        sec = await _load_security(session, "aflt")
    assert sec is not None and sec.ticker == "AFLT"
    assert sec.security_type == "stock"


async def test_load_creates_future_on_demand(session):
    def fake_futures():
        return [
            {"secid": "ONZ6", "shortname": "OZON-12.26", "assetcode": "OZON",
             "lastdeldate": "2026-12-18"},
            {"secid": "SBER", "shortname": "SBER", "assetcode": "", "lastdeldate": None},
        ]

    with patch("app.tech_analysis.service.MOEXClient.fetch_futures_list",
               new=AsyncMock(side_effect=fake_futures)):
        sec = await _load_security(session, "ONZ6")
    assert sec is not None
    assert sec.ticker == "ONZ6"
    assert sec.security_type == "futures"
    assert sec.assetcode == "OZON"
    assert sec.lastdeldate is not None
    # проверяем, что он сохранился в БД
    stored = await session.scalar(select(Security).where(Security.ticker == "ONZ6"))
    assert stored is not None


async def test_load_unknown_returns_none(session):
    with patch("app.tech_analysis.service.MOEXClient.fetch_futures_list",
               new=AsyncMock(return_value=[])):
        sec = await _load_security(session, "ZZZZ")
    assert sec is None


async def test_load_fetures_list_error_returns_none(session):
    with patch("app.tech_analysis.service.MOEXClient.fetch_futures_list",
               new=AsyncMock(side_effect=RuntimeError("network"))):
        sec = await _load_security(session, "ONZ6")
    assert sec is None
