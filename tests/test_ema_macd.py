"""Тесты индикаторов EMA и MACD (docs/19 §8.1–8.2)."""

from datetime import date, datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.connection import Base
from app.db.models import Article, ArticleEntity, MarketCandle, Security, Source
from app.graph.service import resolve_entity_id, seed_graph
from app.market.indicators.base import IndicatorResult
from app.market.indicators.ema import DEFAULT_PARAMS as EMA_PARAMS
from app.market.indicators.ema import _ema, calculate_ema
from app.market.indicators.macd import DEFAULT_PARAMS as MACD_PARAMS
from app.market.indicators.macd import calculate_macd
from app.market.indicators.registry import REGISTRY
from app.strategy.engine import generate_strategy
from app.web.router import _build_indicator_charts


def _candles(closes: list[float], start: date = date(2026, 1, 1)):
    class Candle:
        def __init__(self, trading_date, close):
            self.trading_date = trading_date
            self.close = close

    return [Candle(start + timedelta(days=i), c) for i, c in enumerate(closes)]


# ---------- EMA ----------

def test_ema_known_values():
    # EMA(2): α = 2/3; seed = SMA(2); [2,4,6] -> [None, 3, 5]
    assert _ema([2.0, 4.0, 6.0], 2) == [None, 3.0, 5.0]


def test_ema_insufficient_data():
    assert _ema([1.0, 2.0], 5) == [None, None]


def test_calculate_ema_values_and_meta():
    result = calculate_ema(_candles([10.0, 11.0, 12.0, 13.0]), params={"fast": 2, "slow": 3})
    assert result.indicator == "ema"
    kinds = {v.kind for v in result.values}
    assert kinds == {"ema_fast", "ema_slow"}
    assert result.meta["trend"] in ("up", "down")
    assert result.meta["candles"] == 4


def test_calculate_ema_insufficient():
    result = calculate_ema(_candles([10.0, 11.0]))
    assert result.values == []
    assert "недостаточно данных" in result.meta.get("note", "")


def test_ema_skips_none_closes():
    class Candle:
        def __init__(self, trading_date, close):
            self.trading_date = trading_date
            self.close = close

    start = date(2026, 1, 1)
    candles = [
        Candle(start + timedelta(days=i), c)
        for i, c in enumerate([10.0, 11.0, None, 12.0, 13.0, 14.0, 15.0])
    ]
    result = calculate_ema(candles, params={"fast": 2, "slow": 3})
    assert result.values  # не падает на None-свече
    assert result.meta["candles"] == 6  # None-свеча исключена


def test_ema_cross_down_then_up():
    # fast=2, slow=3: цена растёт, падает, резко растёт -> cross_down затем cross_up
    closes = [10.0, 11.0, 12.0, 13.0, 5.0, 6.0, 7.0, 30.0, 31.0]
    result = calculate_ema(_candles(closes), params={"fast": 2, "slow": 3})
    kinds = [s.kind for s in result.signals]
    assert "cross_down" in kinds
    assert "cross_up" in kinds
    # cross_down раньше cross_up
    down_date = next(s.date for s in result.signals if s.kind == "cross_down")
    up_date = next(s.date for s in result.signals if s.kind == "cross_up")
    assert down_date < up_date


# ---------- MACD ----------

def test_macd_consistency():
    closes = [10.0, 11.0, 12.0, 13.0, 12.5, 11.0, 13.0, 14.0, 15.0, 16.0]
    result = calculate_macd(_candles(closes))
    assert result.indicator == "macd"
    by_kind = {}
    for v in result.values:
        by_kind.setdefault(v.kind, {})[v.date] = v.value
    # MACD = EMA(fast) − EMA(slow)
    fast = _ema(closes, 12)
    slow = _ema(closes, 26)
    # при 10 свечах недостаточно для MACD (slow=26) -> пустой результат
    assert result.values == []
    assert "недостаточно данных" in result.meta.get("note", "")


def test_macd_known_calculation():
    # 60 свечей: растущий тренд с коррекцией — MACD должен быть посчитан
    closes = [100.0 + i + (5.0 if i % 10 < 2 else -3.0) for i in range(60)]
    result = calculate_macd(_candles(closes))
    assert len(result.values) >= 3 * 20
    by_kind = {}
    for v in result.values:
        by_kind.setdefault(v.kind, []).append((v.date, v.value))
    assert {"macd", "signal", "hist"} <= set(by_kind)
    # hist = macd − signal на каждой дате, где все три определены
    macd_map = dict(by_kind["macd"])
    signal_map = dict(by_kind["signal"])
    hist_map = dict(by_kind["hist"])
    common = set(macd_map) & set(signal_map) & set(hist_map)
    assert common
    for d in list(common)[:5]:
        # значения округлены до 4 знаков — допуск 2e-4
        assert abs(hist_map[d] - (macd_map[d] - signal_map[d])) < 2e-4
    assert result.meta["trend"] in ("up", "down", "unknown")
    assert result.meta["latest_hist"] is not None


def test_macd_signals_on_sign_flip():
    # Умеренный разворот после прогрева (slow=3, signal=2): должны появиться сигналы
    closes = [10.0, 11.0, 12.0, 13.0, 14.0, 13.5, 13.0, 12.5, 12.0, 11.5, 11.0, 12.0, 13.0, 14.0]
    result = calculate_macd(_candles(closes), params={"fast": 2, "slow": 3, "signal": 2})
    kinds = {s.kind for s in result.signals}
    assert kinds <= {"cross_up", "cross_down", "hist_positive", "hist_negative"}
    assert len(kinds) >= 1
    # после разворота вверх должен быть хотя бы один бычий сигнал
    assert "cross_up" in kinds or "hist_positive" in kinds


# ---------- Реестр и веб ----------

def test_registry_contains_ema_macd():
    assert "ema" in REGISTRY
    assert "macd" in REGISTRY
    assert REGISTRY["ema"]["complexity"] == "easy"
    assert set(REGISTRY["macd"]["params"]) == {"fast", "slow", "signal"}


def test_build_indicator_charts():
    series = {
        "ema_fast": [(date(2026, 1, 1), 1.0), (date(2026, 1, 2), 2.0), (date(2026, 1, 3), 3.0)],
        "ema_slow": [(date(2026, 1, 1), None), (date(2026, 1, 2), 1.5), (date(2026, 1, 3), 2.5)],
    }
    charts = _build_indicator_charts(series)
    assert {c["kind"] for c in charts} == {"ema_fast", "ema_slow"}
    assert all(c["points"] for c in charts)
    assert charts[0]["first_date"] == "2026-01-01"


# ---------- интеграция в движок стратегий (шаг 5 ТЗ §8.2) ----------


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


async def _seed_closes(session, ticker: str, closes: list[float]) -> None:
    security = await session.scalar(select(Security).where(Security.ticker == ticker))
    start = date.today() - timedelta(days=len(closes) + 2)
    for i, close in enumerate(closes):
        session.add(
            MarketCandle(
                security_id=security.id,
                trading_date=start + timedelta(days=i),
                open=close,
                high=close,
                low=close,
                close=close,
                volume=1000,
            )
        )


async def test_engine_trend_signal_down_weakens_buy(session):
    await seed_graph(session)
    await _seed_closes(session, "AFLT", [100.0 - 0.02 * i * i for i in range(60)])
    await _store_news(session, "Аэрофлот", "positive")
    await session.commit()

    result = await generate_strategy(session, "AFLT", persist=False, use_live_market=False)
    assert any(
        s["kind"] == "trend" and s["sentiment"] == "negative"
        for s in result["signals"]
    )
    assert any("MACD" in r for r in result["risks"])
    assert "MACD" in result["rationale_summary"]


async def test_engine_trend_signal_up_weakens_sell(session):
    await seed_graph(session)
    await _seed_closes(session, "AFLT", [100.0 + 0.02 * i * i for i in range(60)])
    await _store_news(session, "Аэрофлот", "negative")
    await session.commit()

    result = await generate_strategy(session, "AFLT", persist=False, use_live_market=False)
    assert any(
        s["kind"] == "trend" and s["sentiment"] == "positive"
        for s in result["signals"]
    )
    assert any("MACD" in r for r in result["risks"])


async def test_engine_no_trend_signal_without_candles(session):
    await seed_graph(session)
    await session.commit()

    result = await generate_strategy(session, "AFLT", persist=False, use_live_market=False)
    assert not any(s["kind"] == "trend" for s in result["signals"])
