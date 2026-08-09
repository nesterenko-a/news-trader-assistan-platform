from datetime import date, datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.connection import Base
from app.db.models import (
    Article,
    ArticleEntity,
    MarketCandle,
    MarketOpenPosition,
    Security,
    Source,
    security_entity,
)
from app.graph.service import resolve_entity_id, seed_graph
from app.market.oi_data import (
    ensure_futures_security,
    futures_for_security,
    latest_oi_signal,
    nearest_future,
)
from app.strategy.engine import (
    _build_counterarguments,
    _oi_contradicts,
    _oi_verdict_label,
    generate_strategy,
)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as store:
        yield store
    await engine.dispose()


async def _store_news(session, entity_name: str, sentiment: str) -> None:
    source = Source(name="РБК", kind="rss", reputation_score=0.8)
    session.add(source)
    await session.flush()
    entity_id = await resolve_entity_id(session, entity_name)
    article = Article(
        title=f"{entity_name} {sentiment}",
        text="текст новости",
        url=f"https://example.com/{entity_name}",
        source_id=source.id,
        source_reputation=0.8,
        published_at=datetime.now(timezone.utc) - timedelta(hours=2),
        language="ru",
        analysis_version="test",
    )
    session.add(article)
    await session.flush()
    session.add(
        ArticleEntity(
            article_id=article.id,
            entity_id=entity_id,
            sentiment=sentiment,
            impact=0.9,
            snippet=f"Фраза про {entity_name}",
            entity_role="primary",
        )
    )


async def _seed_futures_oi(
    session, closes: list[float], ois: list[int], start: date, volumes: list[float] | None = None
) -> Security:
    security = Security(
        ticker="W4V6", name="WHEAT-10.26", security_type="futures", aliases=[]
    )
    session.add(security)
    await session.flush()
    oil_id = await resolve_entity_id(session, "Нефть")
    assert oil_id is not None
    await session.execute(
        security_entity.insert().values(security_id=security.id, entity_id=oil_id)
    )
    volumes = volumes or [100.0] * len(closes)
    for i, (close, oi) in enumerate(zip(closes, ois)):
        d = start + timedelta(days=i)
        session.add(
            MarketCandle(
                security_id=security.id, trading_date=d, close=close, volume=volumes[i]
            )
        )
        session.add(
            MarketOpenPosition(
                security_id=security.id, trading_date=d, open_position=oi
            )
        )
    await session.commit()
    return security


def test_oi_contradicts_mapping():
    assert _oi_contradicts("strong_bear", "BUY")
    assert _oi_contradicts("long_liquidation", "BUY")
    assert not _oi_contradicts("strong_bull", "BUY")
    assert _oi_contradicts("strong_bull", "SELL")
    assert _oi_contradicts("short_covering", "SELL")
    assert not _oi_contradicts("strong_bear", "SELL")
    assert not _oi_contradicts("strong_bull", "HOLD")


async def test_build_counterarguments_oi_contradiction(session):
    counter, risks = await _build_counterarguments(
        session,
        signals=[],
        verdict="BUY",
        indicator_note=None,
        oi_signal={
            "kind": "strong_bear",
            "note": "Цена падает, OI растёт — открытие коротких позиций",
        },
    )
    assert any(r.startswith("рыночный: OI") for r in risks)
    assert any(c["entity"] == "OI (открытый интерес)" for c in counter)


async def test_latest_oi_signal_asof(session):
    await seed_graph(session)
    start = date.today() - timedelta(days=6)
    await _seed_futures_oi(
        session,
        closes=[100.0, 99.0, 98.0, 99.0],
        ois=[1000, 1100, 1200, 1300],
        start=start,
    )
    security_id = (await session.scalar(select(Security).where(Security.ticker == "W4V6"))).id
    signal = await latest_oi_signal(
        session, security_id, as_of=start + timedelta(days=2)
    )
    assert signal is not None
    assert signal["kind"] == "strong_bear"
    assert signal["date"] == start + timedelta(days=2)


async def test_latest_oi_signal_none_without_data(session):
    security = Security(ticker="W4V6", name="x", security_type="futures", aliases=[])
    session.add(security)
    await session.commit()
    assert await latest_oi_signal(session, security.id) is None


async def test_engine_oi_signal_in_counterarguments(session):
    await seed_graph(session)
    start = date.today() - timedelta(days=6)
    await _seed_futures_oi(
        session,
        closes=[100.0, 99.0, 98.0],
        ois=[1000, 1100, 1200],
        start=start,
    )
    await _store_news(session, "Нефть", "positive")
    await session.commit()

    result = await generate_strategy(
        session, "W4V6", persist=False, use_live_market=False
    )
    assert result["strategy"]["verdict"] == "BUY"
    assert any(
        c["entity"] == "OI (открытый интерес)" for c in result["counterarguments"]
    )
    assert any("рыночный: OI" in r for r in result["risks"])
    assert any("OI" in reason for reason in result["rationale_summary"].split("; "))


async def test_futures_for_security_by_assetcode(session):
    stock = Security(ticker="LKOH", name="ЛУКОЙЛ", security_type="stock", sector="Нефть и газ", aliases=[])
    fut1 = Security(
        ticker="LKOH-6.26", name="LUKOIL-6.26", security_type="futures",
        assetcode="LKOH", lastdeldate=date(2026, 6, 18), aliases=[],
    )
    fut2 = Security(
        ticker="LKOH-9.26", name="LUKOIL-9.26", security_type="futures",
        assetcode="LKOH", lastdeldate=date(2026, 9, 17), aliases=[],
    )
    other = Security(ticker="SBER", name="Сбер", security_type="stock", aliases=[])
    session.add_all([stock, fut1, fut2, other])
    await session.commit()

    futures = await futures_for_security(session, "LKOH")
    assert [f.ticker for f in futures] == ["LKOH-6.26", "LKOH-9.26"]
    assert await futures_for_security(session, "SBER") == []

    nearest = await nearest_future(session, "LKOH", as_of=date(2026, 5, 1))
    assert nearest.ticker == "LKOH-6.26"
    nearest_past = await nearest_future(session, "LKOH", as_of=date(2027, 1, 1))
    assert nearest_past.ticker == "LKOH-9.26"


async def test_engine_stock_uses_nearest_future_oi(session):
    await seed_graph(session)
    stock = await session.scalar(select(Security).where(Security.ticker == "LKOH"))
    oil_id = await resolve_entity_id(session, "Нефть")
    await session.execute(
        security_entity.insert().values(security_id=stock.id, entity_id=oil_id)
    )
    start = date.today() - timedelta(days=6)
    fut = await _seed_futures_oi(
        session,
        closes=[100.0, 99.0, 98.0],
        ois=[1000, 1100, 1200],
        start=start,
    )
    fut.assetcode = "LKOH"
    fut.lastdeldate = date.today() + timedelta(days=30)
    await session.commit()

    await _store_news(session, "Нефть", "positive")
    await session.commit()

    result = await generate_strategy(
        session, "LKOH", persist=False, use_live_market=False
    )
    assert any(s["kind"] == "oi" for s in result["signals"])
    oi_signal = next(s for s in result["signals"] if s["kind"] == "oi")
    assert oi_signal["entity"] == f"OI {fut.ticker}"
    assert oi_signal["weight"] == 0.0
    assert any("рыночный: OI" in r for r in result["risks"])


async def test_ensure_futures_security_normalizes_string_date(session):
    fut = await ensure_futures_security(
        session, "W4V6", "WHEAT-10.26", assetcode="LKOH", lastdeldate="2026-08-28"
    )
    assert fut.assetcode == "LKOH"
    assert fut.lastdeldate == date(2026, 8, 28)
    assert fut.security_type == "futures"


async def _set_volumes(session, ticker: str, volumes: list[float]) -> None:
    fut = await session.scalar(select(Security).where(Security.ticker == ticker))
    candles = (
        await session.scalars(
            select(MarketCandle).where(MarketCandle.security_id == fut.id)
        )
    ).all()
    for candle, v in zip(candles, volumes):
        candle.volume = v
    await session.commit()


async def test_engine_oi_contradiction_volume_down_weakens(session):
    await seed_graph(session)
    start = date.today() - timedelta(days=6)
    await _seed_futures_oi(
        session,
        closes=[100.0, 99.0, 98.0],
        ois=[1000, 1100, 1200],
        start=start,
        volumes=[100.0, 200.0, 300.0],
    )
    await _store_news(session, "Нефть", "positive")
    await session.commit()

    r_up = await generate_strategy(session, "W4V6", persist=False, use_live_market=False)
    await _set_volumes(session, "W4V6", [300.0, 200.0, 100.0])
    r_down = await generate_strategy(session, "W4V6", persist=False, use_live_market=False)

    assert r_up["strategy"]["verdict"] == "BUY"
    # падающий объём ослабляет противоречие слабее: ×0.92 против ×0.85
    assert r_up["strategy"]["net_score"] < r_down["strategy"]["net_score"]


async def test_engine_oi_confirmation_volume_up_amplifies(session):
    await seed_graph(session)
    start = date.today() - timedelta(days=6)
    await _seed_futures_oi(
        session,
        closes=[100.0, 101.0, 102.0],
        ois=[1000, 1100, 1200],
        start=start,
        volumes=[100.0, 200.0, 300.0],
    )
    await _store_news(session, "Нефть", "positive")
    await session.commit()

    r_up = await generate_strategy(session, "W4V6", persist=False, use_live_market=False)
    await _set_volumes(session, "W4V6", [300.0, 200.0, 100.0])
    r_down = await generate_strategy(session, "W4V6", persist=False, use_live_market=False)

    assert r_up["strategy"]["verdict"] == "BUY"
    # согласный сигнал с растущим объёмом усиливает: ×1.05 против без усиления
    assert r_up["strategy"]["net_score"] > r_down["strategy"]["net_score"]


def test_oi_verdict_label():
    assert _oi_verdict_label("strong_bull") == "покупка"
    assert _oi_verdict_label("strong_bear") == "продажа"
    assert _oi_verdict_label("long_liquidation") == "покупка"
    assert _oi_verdict_label("short_covering") == "продажа"
    assert _oi_verdict_label("bearish_setup") == "продажа"
    assert _oi_verdict_label("bullish_setup") == "покупка"


async def test_oi_risk_contains_indicator_verdict(session):
    counter, risks = await _build_counterarguments(
        session,
        signals=[],
        verdict="BUY",
        indicator_note=None,
        oi_signal={
            "kind": "strong_bear",
            "note": "Цена падает ↓, OI растёт ↑ — открытие − коротких позиций",
        },
    )
    assert any("Вердикт по индикатору: продажа" in r for r in risks)


async def test_engine_volume_profile_signal(session):
    await seed_graph(session)
    stock = await session.scalar(select(Security).where(Security.ticker == "LKOH"))
    oil_id = await resolve_entity_id(session, "Нефть")
    await session.execute(
        security_entity.insert().values(security_id=stock.id, entity_id=oil_id)
    )
    start = date.today() - timedelta(days=30)
    for i in range(30):
        d = start + timedelta(days=i)
        session.add(
            MarketCandle(
                security_id=stock.id, trading_date=d,
                open=100 + i, high=102 + i, low=98 + i, close=101 + i, volume=1000,
            )
        )
    await session.commit()
    await _store_news(session, "Нефть", "positive")
    await session.commit()

    result = await generate_strategy(
        session, "LKOH", persist=False, use_live_market=False
    )
    vp_signals = [s for s in result["signals"] if s["kind"] == "vp"]
    assert vp_signals
    assert "POC" in vp_signals[0]["path"][0]


async def test_engine_support_resistance_signal(session):
    """Уровни S/R попадают в «Сигналы» движка (kind='sr', вес 0)."""
    await seed_graph(session)
    stock = await session.scalar(select(Security).where(Security.ticker == "LKOH"))
    oil_id = await resolve_entity_id(session, "Нефть")
    await session.execute(
        security_entity.insert().values(security_id=stock.id, entity_id=oil_id)
    )
    # Боковик с вариациями (фракталы образуются), 60 свечей
    start = date.today() - timedelta(days=60)
    for i in range(60):
        d = start + timedelta(days=i)
        base = 100 + (i % 10)
        session.add(
            MarketCandle(
                security_id=stock.id, trading_date=d,
                open=base - 1, high=base + 2, low=base - 2, close=base, volume=1000,
            )
        )
    await session.commit()
    await _store_news(session, "Нефть", "positive")
    await session.commit()

    result = await generate_strategy(
        session, "LKOH", persist=False, use_live_market=False
    )
    sr_signals = [s for s in result["signals"] if s["kind"] == "sr"]
    assert sr_signals, "должен появиться сигнал kind='sr'"
    assert "R" in sr_signals[0]["path"][0] or "S" in sr_signals[0]["path"][0]
