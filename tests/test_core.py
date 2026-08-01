from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.connection import Base
from app.collectors.rss import _parse_date
from app.db.models import Article, ArticleEntity, Source, Strategy
from scripts.collect_news import _mention_check, _parse_since, _within_since
from app.graph.service import (
    find_influence_paths,
    resolve_entity_id,
    seed_graph,
    security_entity_ids,
)
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


async def test_seed_and_influence_paths(session):
    await seed_graph(session)

    oil_id = await resolve_entity_id(session, "Нефть")
    aero_id = await resolve_entity_id(session, "Аэрофлот")
    assert oil_id is not None
    assert aero_id is not None

    aero_entities = await security_entity_ids(session, 1)
    assert aero_id in aero_entities

    paths = await find_influence_paths(session, oil_id, aero_id)
    assert paths
    best = paths[0]
    assert best.sign == -1.0
    assert best.entities[0] == "Нефть"
    assert best.entities[-1] == "Аэрофлот"

    lukoil_id = await resolve_entity_id(session, "Лукойл")
    oil_paths = await find_influence_paths(session, oil_id, lukoil_id)
    assert oil_paths
    assert oil_paths[0].sign == 1.0


class FakeMOEX:
    async def fetch_quote(self, ticker: str):
        return {
            "ticker": ticker,
            "price": 100.0,
            "open": 100.0,
            "high": 105.0,
            "low": 98.0,
            "volume": 1000,
        }

    async def fetch_daily_closes(self, ticker: str, days: int = 60):
        return [
            100.2, 99.8, 100.5, 100.1, 99.6, 100.8, 100.3, 99.9, 100.4, 100.0,
            99.7, 100.6, 100.2, 99.8, 100.5, 100.1, 99.9, 100.7, 100.2, 100.0,
            99.8, 100.4, 100.1, 99.7, 100.6, 100.3, 99.9, 100.5, 100.2, 100.1,
        ]


async def _store_news(session, url: str, entity_name: str, sentiment: str):
    source = Source(name="РБК", kind="rss", reputation_score=0.8)
    session.add(source)
    await session.flush()
    entity_id = await resolve_entity_id(session, entity_name)
    article = Article(
        title=f"{entity_name} {sentiment}",
        text="текст новости",
        url=url,
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


async def test_oil_news_hits_aviation_and_oil_company(session, monkeypatch):
    monkeypatch.setattr("app.market.moex.MOEXClient", lambda: FakeMOEX())
    await seed_graph(session)

    await _store_news(session, "http://test.ru/oil", "Нефть", "positive")
    await session.commit()

    aero_result = await generate_strategy(session, "AFLT")
    assert aero_result["strategy"]["verdict"] == "SELL"
    assert aero_result["strategy_id"] is not None

    lukoil_result = await generate_strategy(session, "LKOH")
    assert lukoil_result["strategy"]["verdict"] == "BUY"


async def test_insufficient_data(session, monkeypatch):
    monkeypatch.setattr("app.market.moex.MOEXClient", lambda: FakeMOEX())
    await seed_graph(session)

    result = await generate_strategy(session, "SBER")
    assert result["strategy"]["verdict"] == "INSUFFICIENT_DATA"


async def test_generate_strategy_without_persist(session, monkeypatch):
    monkeypatch.setattr("app.market.moex.MOEXClient", lambda: FakeMOEX())
    await seed_graph(session)

    await _store_news(session, "http://test.ru/oil3", "Нефть", "positive")
    await session.commit()

    result = await generate_strategy(session, "AFLT", persist=False)
    assert result["strategy"]["verdict"] == "SELL"
    assert "strategy_id" not in result

    strategies = (await session.scalars(select(Strategy))).all()
    assert strategies == []


async def test_collect_news_date_filter(session):
    since = _parse_since(SimpleNamespace(from_date="2026-01-01", days=0))
    assert since == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert _within_since(datetime(2026, 2, 1, tzinfo=timezone.utc), since)
    assert not _within_since(datetime(2025, 12, 31, tzinfo=timezone.utc), since)
    assert _within_since(None, since)
    assert _within_since(datetime(2025, 1, 1, tzinfo=timezone.utc), None)

    by_days = _parse_since(SimpleNamespace(from_date="", days=5))
    assert by_days is not None
    assert by_days > datetime(2025, 1, 1, tzinfo=timezone.utc)

    no_filter = _parse_since(SimpleNamespace(from_date="", days=0))
    assert no_filter is None


async def test_rss_date_parsing():
    assert _parse_date("Tue, 29 Jul 2026 12:34:56 +0300") == datetime(
        2026, 7, 29, 9, 34, 56, tzinfo=timezone.utc
    )
    assert _parse_date("2026-07-29T12:34:56+03:00") == datetime(
        2026, 7, 29, 9, 34, 56, tzinfo=timezone.utc
    )
    assert _parse_date("Wed, 30 Jul 2026 09:00:00 GMT") == datetime(
        2026, 7, 30, 9, 0, 0, tzinfo=timezone.utc
    )
    assert _parse_date("") is None
    assert _parse_date("garbage") is None


async def test_mention_check_restricted_entities(session):
    await seed_graph(session)

    assert await _mention_check(session, "Аэрофлот увеличил пассажиропоток", {"Аэрофлот"})
    assert not await _mention_check(session, "Нефть подорожала", {"Аэрофлот"})
    assert await _mention_check(session, "Нефть подорожала", None)
    assert await _mention_check(session, "Магнит увеличил выручку", {"Магнит"})
    assert not await _mention_check(session, "Землетрясение магнитудой 7,1", {"Магнит"})
