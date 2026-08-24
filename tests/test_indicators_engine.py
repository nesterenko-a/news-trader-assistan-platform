"""Тесты интеграции индикаторов bollinger/atr/adx в движок стратегий (docs/19)."""

from datetime import date, datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.connection import Base
from app.db.models import (
    Article,
    ArticleEntity,
    MarketCandle,
    Security,
    Source,
)
from app.graph.service import resolve_entity_id, seed_graph
from app.strategy.engine import generate_strategy


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


def _signals_by_kind(result, kind: str) -> list[dict]:
    return [s for s in result["signals"] if s["kind"] == kind]


# ---------- Полосы Боллинджера ----------

async def test_engine_bollinger_overbought_weakens_buy(session):
    await seed_graph(session)
    # плоская база сжимает полосы, финальный всплеск выводит цену за верхнюю полосу
    await _seed_closes(
        session, "AFLT", [100.0] * 50 + [100, 101, 104, 108, 113, 119, 126]
    )
    await _store_news(session, "Аэрофлот", "positive")
    await session.commit()

    result = await generate_strategy(session, "AFLT", persist=False, use_live_market=False)
    bb = _signals_by_kind(result, "bollinger")
    assert bb and "верхней полосой" in bb[0]["path"][0]
    assert "выше верхней полосы" in result["rationale_summary"]


async def test_engine_bollinger_oversold_weakens_sell(session):
    await seed_graph(session)
    # плоская база + резкое падение в конце — цена за нижней полосой
    await _seed_closes(
        session, "AFLT", [100.0] * 50 + [100, 99, 96, 92, 87, 81, 74]
    )
    await _store_news(session, "Аэрофлот", "negative")
    await session.commit()

    result = await generate_strategy(session, "AFLT", persist=False, use_live_market=False)
    bb = _signals_by_kind(result, "bollinger")
    assert bb and "нижней полосой" in bb[0]["path"][0]
    assert "ниже нижней полосы" in result["rationale_summary"]


async def test_engine_bollinger_flat_no_signal(session):
    await seed_graph(session)
    await _seed_closes(session, "AFLT", [100.0] * 60)
    await _store_news(session, "Аэрофлот", "positive")
    await session.commit()

    result = await generate_strategy(session, "AFLT", persist=False, use_live_market=False)
    # на плоском ряду цена внутри полос — сигнал полос не выдаётся
    assert not _signals_by_kind(result, "bollinger")


# ---------- ATR ----------

async def test_engine_atr_volatility_context(session):
    await seed_graph(session)
    await _seed_closes(session, "AFLT", [100.0 + 0.3 * i for i in range(60)])
    await _store_news(session, "Аэрофлот", "positive")
    await session.commit()

    result = await generate_strategy(session, "AFLT", persist=False, use_live_market=False)
    atr = _signals_by_kind(result, "atr")
    assert atr and "ATR" in atr[0]["path"][0]
    assert "% от цены" in atr[0]["path"][0]


# ---------- ADX ----------

async def test_engine_adx_strong_trend_against_buy_weakens(session):
    await seed_graph(session)
    # сильный нисходящий тренд (+DI < −DI, ADX высокий) + бычий новостной фон
    await _seed_closes(session, "AFLT", [100.0 - 0.3 * i for i in range(60)])
    await _store_news(session, "Аэрофлот", "positive")
    await session.commit()

    result = await generate_strategy(session, "AFLT", persist=False, use_live_market=False)
    adx = _signals_by_kind(result, "adx")
    assert adx and adx[0]["sentiment"] == "negative"
    assert "сильный тренд по ADX против сигнала" in result["rationale_summary"]


async def test_engine_adx_agrees_does_not_dampen(session):
    await seed_graph(session)
    # сильный восходящий тренд + бычий новостной фон — согласуется, без ослабления
    await _seed_closes(session, "AFLT", [100.0 + 0.3 * i for i in range(60)])
    await _store_news(session, "Аэрофлот", "positive")
    await session.commit()

    result = await generate_strategy(session, "AFLT", persist=False, use_live_market=False)
    adx = _signals_by_kind(result, "adx")
    assert adx and adx[0]["sentiment"] == "positive"
    assert "сильный тренд по ADX против сигнала" not in result["rationale_summary"]


async def test_engine_indicators_insufficient_candles(session):
    await seed_graph(session)
    await _seed_closes(session, "AFLT", [100.0 - i for i in range(10)])
    await _store_news(session, "Аэрофлот", "positive")
    await session.commit()

    result = await generate_strategy(session, "AFLT", persist=False, use_live_market=False)
    # мало свечей — ни bollinger, ни atr, ни adx-тренд не вычисляются
    assert not _signals_by_kind(result, "bollinger")
    assert not _signals_by_kind(result, "atr")
    assert not _signals_by_kind(result, "adx")
