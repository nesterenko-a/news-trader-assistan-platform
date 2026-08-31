"""Тесты on-demand создания фьючерсной бумаги для «Теханализ в LLM»."""

from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.connection import Base
from app.db.models import MarketOpenPositionClientGroup, Security
from app.tech_analysis.request_builder import build_analysis_request
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


async def test_request_includes_recent_client_positions_history(session):
    future = Security(ticker="ONZ6", name="OZON-12.26", market="MOEX", security_type="futures", sector="", aliases=[])
    session.add(future)
    await session.flush()
    for offset in range(25):
        trading_date = date.today() - timedelta(days=offset)
        for group, long_pos, short_pos in (
            ("physical", 1000 + offset, 700 + offset),
            ("juridical", 300 + offset, 600 + offset),
        ):
            session.add(
                MarketOpenPositionClientGroup(
                    security_id=future.id,
                    trading_date=trading_date,
                    client_group=group,
                    long_pos=long_pos,
                    short_pos=short_pos,
                    net_pos=long_pos - short_pos,
                    participants=0,
                    summary=1,
                )
            )
    await session.commit()

    request = await build_analysis_request(session, future)

    assert "### Клиентские позиции" in request["request_md"]
    assert "22 торговых дат" in request["request_md"]
    assert f"| {date.today()} | 1000 | 700 | 300 | 300 | 600 | -300 |" in request["request_md"]
    assert f"| {date.today() - timedelta(days=21)} | 1021 | 721 | 300 | 321 | 621 | -300 |" in request["request_md"]
    assert f"| {date.today() - timedelta(days=22)} | 1022 | 722 | 300 | 322 | 622 | -300 |" not in request["request_md"]
