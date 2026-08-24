"""Тесты REST-веток индикаторов bollinger/atr/adx (docs/19 §8.3/8.8/8.10)."""

from datetime import date, timedelta

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.routes.indicators import calculate_indicator
from app.db.connection import Base
from app.db.models import MarketCandle, Security


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as store:
        yield store
    await engine.dispose()


async def test_api_bollinger_route(session):
    await _seed(session)
    await session.commit()
    res = await calculate_indicator(
        "bollinger",
        ticker="TEST",
        period=20,
        k=2,
        from_=date(2026, 1, 1),
        to=date(2026, 4, 1),
        limit=None,
        session=session,
    )
    assert res["indicator"] == "bollinger"
    assert res["params"]["period"] == 20
    assert res["meta"]["latest_upper"] is not None
    assert res["meta"]["latest_lower"] is not None
    # values сериализуются в ISO-строки (дата без времени)
    dates = {v["date"] for v in res["values"]}
    assert all("T" not in d for d in dates)
    # первое значение — на период-той свече (индекс period), даты согласованы
    first_date = sorted(dates)[0]
    assert first_date == "2026-01-20"


async def test_api_atr_route(session):
    await _seed(session)
    await session.commit()
    res = await calculate_indicator(
        "atr",
        ticker="TEST",
        period=14,
        from_=date(2026, 1, 1),
        to=date(2026, 4, 1),
        limit=None,
        session=session,
    )
    assert res["indicator"] == "atr"
    assert res["meta"]["latest_atr"] is not None
    assert res["meta"]["atr_pct"] is not None
    first_dates = sorted({v["date"] for v in res["values"]})
    assert first_dates[0] == "2026-01-15"


async def test_api_adx_route(session):
    await _seed(session)
    await session.commit()
    res = await calculate_indicator(
        "adx",
        ticker="TEST",
        period=14,
        from_=date(2026, 1, 1),
        to=date(2026, 4, 1),
        limit=None,
        session=session,
    )
    assert res["indicator"] == "adx"
    assert res["meta"]["latest_adx"] is not None
    # в восходящем тренде +DI выше −DI
    assert res["meta"]["latest_plus_di"] > res["meta"]["latest_minus_di"]
    first_dates = sorted({v["date"] for v in res["values"]})
    assert first_dates[0] == "2026-01-15"


async def test_api_unknown_ticker(session):
    await session.commit()
    from fastapi import HTTPException

    try:
        await calculate_indicator(
            "bollinger",
            ticker="NOPE",
            period=20,
            from_=date(2026, 1, 1),
            to=date(2026, 4, 1),
            limit=None,
            session=session,
        )
        assert False, "должен подняться HTTPException"
    except HTTPException as e:
        assert e.status_code == 400


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
