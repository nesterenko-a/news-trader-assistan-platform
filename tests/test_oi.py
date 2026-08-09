from datetime import date, timedelta

from app.market.indicators.oi import calculate_oi


def _series(closes: list[float], ois: list[int]) -> list[tuple[date, float | None, int | None]]:
    d0 = date(2026, 8, 1)
    return [
        (d0 + timedelta(days=i), c, o) for i, (c, o) in enumerate(zip(closes, ois))
    ]


def test_strong_bull():
    res = calculate_oi(_series([100, 101, 102], [1000, 1100, 1200]))
    kinds = [s.kind for s in res.signals]
    assert "strong_bull" in kinds
    assert all(s.severity == "strong" for s in res.signals if s.kind == "strong_bull")


def test_strong_bear():
    res = calculate_oi(_series([104, 103, 102], [1000, 1100, 1200]))
    assert "strong_bear" in [s.kind for s in res.signals]


def test_long_liquidation():
    res = calculate_oi(_series([104, 103, 102], [1200, 1100, 1000]))
    assert "long_liquidation" in [s.kind for s in res.signals]
    assert res.signals[0].severity == "warning"


def test_short_covering():
    res = calculate_oi(_series([100, 101, 102], [1200, 1100, 1000]))
    assert "short_covering" in [s.kind for s in res.signals]


def test_volume_up_confirms_signal():
    d0 = date(2026, 8, 1)
    series = [
        (d0 + timedelta(days=i), c, o, v)
        for i, (c, o, v) in enumerate(
            zip([100, 101, 102], [1000, 1100, 1200], [100, 200, 300])
        )
    ]
    res = calculate_oi(series)
    bull = [s for s in res.signals if s.kind == "strong_bull"]
    assert bull
    assert all(s.volume == "up" for s in bull)
    assert "объём растёт ↑" in bull[0].note


def test_volume_down_weakens_signal():
    d0 = date(2026, 8, 1)
    series = [
        (d0 + timedelta(days=i), c, o, v)
        for i, (c, o, v) in enumerate(
            zip([100, 101, 102], [1000, 1100, 1200], [300, 200, 100])
        )
    ]
    res = calculate_oi(series)
    bull = [s for s in res.signals if s.kind == "strong_bull"]
    assert bull
    assert all(s.volume == "down" for s in bull)
    assert "объём падает ↓" in bull[0].note


def test_no_volume_no_flag():
    res = calculate_oi(_series([100, 101, 102], [1000, 1100, 1200]))
    bull = [s for s in res.signals if s.kind == "strong_bull"]
    assert bull
    assert all(s.volume is None for s in bull)


def test_bearish_setup_price_flat_oi_up():
    res = calculate_oi(_series([100, 100, 100], [1000, 1100, 1200]))
    signals = [s for s in res.signals if s.kind == "bearish_setup"]
    assert signals
    assert all(s.severity == "warning" for s in signals)
    assert "→" in signals[0].note


def test_bullish_setup_price_flat_oi_down():
    res = calculate_oi(_series([100, 100, 100], [1200, 1100, 1000]))
    signals = [s for s in res.signals if s.kind == "bullish_setup"]
    assert signals
    assert all(s.severity == "warning" for s in signals)
    assert "→" in signals[0].note


def test_notes_contain_arrows():
    res = calculate_oi(_series([100, 101, 102], [1000, 1100, 1200]))
    note = next(s.note for s in res.signals if s.kind == "strong_bull")
    assert "↑" in note
    assert "+" in note


def test_no_signal_below_threshold():
    res = calculate_oi(_series([100, 101, 102], [1000, 1005, 1010]))
    assert res.signals == []


def test_custom_threshold():
    res = calculate_oi(
        _series([100, 101, 102], [1000, 1005, 1010]),
        params={"oi_change_threshold_pct": 0.4},
    )
    assert "strong_bull" in [s.kind for s in res.signals]


def test_missing_oi_day_skipped():
    d0 = date(2026, 8, 1)
    series = [
        (d0, 100.0, 1000),
        (d0 + timedelta(days=1), 101.0, None),
        (d0 + timedelta(days=2), 102.0, 1100),
    ]
    res = calculate_oi(series)
    assert len([v for v in res.values if v.kind == "oi"]) == 2
    assert res.signals


def test_values_and_meta():
    res = calculate_oi(_series([100, 101], [1000, 1100]))
    assert res.indicator == "oi"
    kinds = {v.kind for v in res.values}
    assert kinds == {"oi", "oi_change_pct"}
    assert res.meta["candles"] == 2
    assert res.params["oi_change_threshold_pct"] == 1.0


import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.db.models import Base, Security
from app.market import oi_data


@pytest_asyncio.fixture
async def oi_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as store:
        yield store
    await engine.dispose()


async def test_sync_oi_also_updates_prices(oi_session, monkeypatch):
    """sync_security_oi сохраняет и открытые позиции, и свечи цен (п.1)."""
    from datetime import date, timedelta
    from app.market.moex import MOEXClient

    rows = [
        {
            "date": date.today() - timedelta(days=1),
            "open": 100.0, "high": 105.0, "low": 99.0, "close": 104.0,
            "volume": 1000,
            "open_position": 500, "open_position_value": 52000.0,
            "shortname": "WHEAT-10.26",
        }
    ]

    async def fake_fetch(self, ticker, from_date, till_date):
        return rows

    monkeypatch.setattr(MOEXClient, "fetch_open_positions", fake_fetch)
    inserted = await oi_data.sync_security_oi(oi_session, "W4V6", days=5)
    assert inserted == 1

    security = await oi_session.scalar(
        select(Security).where(Security.ticker == "W4V6")
    )
    assert security is not None
    from app.db.models import MarketCandle, MarketOpenPosition

    candle = await oi_session.scalar(select(MarketCandle))
    assert candle is not None
    assert candle.close == 104.0 and candle.high == 105.0
    op = await oi_session.scalar(select(MarketOpenPosition))
    assert op is not None and op.open_position == 500


async def test_sync_oi_client_groups(oi_session, monkeypatch):
    """sync_security_oi сохраняет OI по группам клиентов (физ/юр) по assetcode (п.3)."""
    from datetime import date, timedelta
    from app.market.moex import MOEXClient

    rows = [
        {
            "date": date.today() - timedelta(days=1),
            "open": 100.0, "high": 105.0, "low": 99.0, "close": 104.0,
            "volume": 1000,
            "open_position": 500, "open_position_value": 52000.0,
            "shortname": "WHEAT-10.26",
        }
    ]
    groups = {
        "date": date.today() - timedelta(days=1),
        "physical_long": 28410, "physical_short": 17634,
        "juridical_long": 828, "juridical_short": 11604,
        "summary": 58476,
        "physical_participants": 1072, "juridical_participants": 5,
        "participants_summary": 1244,
    }

    async def fake_fetch(self, ticker, from_date, till_date):
        return rows

    async def fake_groups(self, assetcode, trade_date):
        return groups

    monkeypatch.setattr(MOEXClient, "fetch_open_positions", fake_fetch)
    monkeypatch.setattr(MOEXClient, "fetch_open_positions_client_groups", fake_groups)
    await oi_data.sync_security_oi(
        oi_session, "W4V6", days=5,
        futures_meta={"W4V6": {"assetcode": "WHEAT", "lastdeldate": None}},
    )

    from app.db.models import MarketOpenPositionClientGroup

    recs = (await oi_session.scalars(select(MarketOpenPositionClientGroup))).all()
    assert len(recs) == 2
    by_group = {r.client_group: r for r in recs}
    ph = by_group["physical"]
    ju = by_group["juridical"]
    assert ph.long_pos == 28410 and ph.short_pos == 17634 and ph.net_pos == 10776
    assert ju.long_pos == 828 and ju.short_pos == 11604 and ju.net_pos == -10776
    assert ph.participants == 1072 and ju.participants == 5


def test_delta_level():
    from app.market.oi_data import _delta_level

    cases = [
        (None, "−"), (0.5, "−"), (1.9, "−"), (2.0, "↑"), (4.9, "↑"),
        (5.0, "↑↑"), (9.9, "↑↑"), (10.0, "↑↑↑"), (25.0, "↑↑↑"),
        (-1.0, "−"), (-2.0, "↓"), (-4.9, "↓"), (-5.0, "↓↓"),
        (-9.9, "↓↓"), (-10.0, "↓↓↓"), (-30.0, "↓↓↓"),
    ]
    for pct, expected in cases:
        level, arrows, cls = _delta_level(pct)
        assert arrows == expected, f"{pct}: {arrows} != {expected}"


async def test_client_groups_series_delta(oi_session):
    """Δ OI день-к-дню: первая дата — «−», далее по порогам."""
    from app.db.models import Security, MarketOpenPositionClientGroup
    from datetime import date, timedelta
    from app.market.oi_data import client_groups_series

    sec = Security(ticker="W4V6", name="Пшеница", security_type="futures", assetcode="WHEAT")
    oi_session.add(sec)
    await oi_session.flush()

    base = date(2026, 8, 3)
    # 1-й день: ph long+short = 100 → 2-й день 105 (рост 5% → ↑↑)
    data = [
        (base, 60, 40, 30, 20),
        (base + timedelta(days=1), 65, 40, 30, 20),  # ph 105 vs 100 = +5% → ↑↑; юр 50→50 → −
    ]
    for i, (d, phl, phs, jul, jus) in enumerate(data):
        for group, long, short in (("physical", phl, phs), ("juridical", jul, jus)):
            oi_session.add(MarketOpenPositionClientGroup(
                security_id=sec.id, trading_date=d, client_group=group,
                long_pos=long, short_pos=short, net_pos=long - short,
                participants=0, summary=100 + i,
            ))
    await oi_session.commit()

    series = await client_groups_series(oi_session, sec.id)
    assert len(series) == 2
    assert series[0]["delta"]["physical"]["arrows"] == "−"
    d2 = series[1]["delta"]["physical"]
    assert d2["arrows"] == "↑↑" and d2["pct"] == 5.0, d2
    assert series[1]["delta"]["juridical"]["arrows"] == "−"  # 51→51
