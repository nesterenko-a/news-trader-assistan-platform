"""Тесты веб-вкладок индикаторов bollinger/atr/adx на /indicators (docs/19)."""

from datetime import date, timedelta

from fastapi import Request
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.connection import Base
from app.db.models import MarketCandle, Security
from app.web.router import indicators_page


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as store:
        yield store
    await engine.dispose()


async def _seed(session) -> None:
    security = Security(
        ticker="TEST",
        name="Тестовая бумага",
        security_type="stock",
        sector="металлургия",
    )
    session.add(security)
    await session.flush()
    start = date(2026, 1, 1)
    for i in range(60):
        base = 100.0 + 0.3 * i
        session.add(
            MarketCandle(
                security_id=security.id,
                trading_date=start + timedelta(days=i),
                open=base,
                high=base + 1.0,
                low=base - 1.0,
                close=base,
                volume=1000,
            )
        )


def _request(name: str, ticker: str = "TEST") -> Request:
    qs = f"name={name}&ticker={ticker}".encode()
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/indicators",
            "query_string": qs,
            "headers": [],
            "server": ("test", 80),
            "client": ("test", 80),
            "scheme": "http",
        }
    )


async def test_indicators_bollinger_tab(session):
    await _seed(session)
    await session.commit()
    resp = await indicators_page(
        _request("bollinger"),
        name="bollinger",
        ticker="TEST",
        from_=date(2026, 1, 1),
        to=date(2026, 4, 1),
        bb_period=20,
        bb_k=2,
        session=session,
    )
    html = resp.body.decode()
    assert "Полосы Боллинджера — Тестовая бумага (TEST)" in html
    assert "middle=" in html and "upper=" in html and "lower=" in html
    assert "svg" in html
    assert "Бумага не найдена" not in html


async def test_indicators_atr_tab(session):
    await _seed(session)
    await session.commit()
    resp = await indicators_page(
        _request("atr"),
        name="atr",
        ticker="TEST",
        from_=date(2026, 1, 1),
        to=date(2026, 4, 1),
        atr_period=14,
        session=session,
    )
    html = resp.body.decode()
    assert "ATR — Тестовая бумага (TEST)" in html
    assert "ATR=" in html and "ATR % от цены=" in html
    assert "Бумага не найдена" not in html


async def test_indicators_adx_tab(session):
    await _seed(session)
    await session.commit()
    resp = await indicators_page(
        _request("adx"),
        name="adx",
        ticker="TEST",
        from_=date(2026, 1, 1),
        to=date(2026, 4, 1),
        adx_period=14,
        session=session,
    )
    html = resp.body.decode()
    assert "ADX/DI — Тестовая бумага (TEST)" in html
    assert "ADX=" in html and "+DI=" in html and "−DI=" in html
    assert "Бумага не найдена" not in html


async def test_indicators_bollinger_unknown_ticker(session):
    await session.commit()
    resp = await indicators_page(
        _request("bollinger", ticker="NOPE"),
        name="bollinger",
        ticker="NOPE",
        from_=date(2026, 1, 1),
        to=date(2026, 4, 1),
        bb_period=20,
        bb_k=2,
        session=session,
    )
    html = resp.body.decode()
    assert "Бумага не найдена" in html
