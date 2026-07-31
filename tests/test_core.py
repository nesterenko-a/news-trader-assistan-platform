from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.connection import Base
from app.db.models import Article, ArticleEntity, Source
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
