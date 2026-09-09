from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.connection import Base
from app.db.models import (
    Article,
    Entity,
    GraphCandidate,
    Influence,
    Source,
    User,
)
from app.graph.candidates import approve_candidate, record_suggestions, reject_candidate


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as store:
        yield store
    await engine.dispose()


async def _article(session, source_id: int, suffix: str) -> Article:
    item = Article(
        title=f"Новость {suffix}",
        text="Текст новости",
        url=f"https://example.test/{suffix}",
        source_id=source_id,
        published_at=datetime.now(timezone.utc),
    )
    session.add(item)
    await session.flush()
    return item


@pytest.mark.asyncio
async def test_suggestions_aggregate_and_become_pending(session):
    source = Source(name="Источник", kind="rss")
    first = Entity(name="Нефть", type="commodity")
    second = Entity(name="Нефтегаз", type="sector")
    session.add_all([source, first, second])
    await session.flush()
    entities = {"нефть": first.id, "нефтегаз": second.id}
    one = await _article(session, source.id, "one")
    two = await _article(session, source.id, "two")
    suggestion = {
        "from_entity": "Нефть",
        "to_entity": "Нефтегаз",
        "direction": "positive",
        "strength": "strong",
        "kind": "direct",
        "confidence": 0.8,
        "rationale": "Рост нефти поддерживает сектор",
        "quote": "Цена нефти выросла",
    }

    assert await record_suggestions(session, one.id, [suggestion], entities) == 1
    assert await record_suggestions(session, two.id, [suggestion], entities) == 1
    candidate = (await session.scalars(select(GraphCandidate))).one()
    assert candidate.evidence_count == 2
    assert candidate.status == "pending"
    assert candidate.strength == "strong"
    assert await record_suggestions(session, two.id, [suggestion], entities) == 0
    assert candidate.evidence_count == 2


@pytest.mark.asyncio
async def test_strength_uses_majority_then_conservative_tiebreak(session):
    source = Source(name="Источник", kind="rss")
    first = Entity(name="Сырьё", type="commodity")
    second = Entity(name="Сектор", type="sector")
    session.add_all([source, first, second])
    await session.flush()
    entities = {"сырьё": first.id, "сектор": second.id}
    base = {
        "from_entity": "Сырьё", "to_entity": "Сектор", "direction": "positive",
        "kind": "direct", "confidence": 0.8, "rationale": "Связь", "quote": "Цитата",
    }
    for suffix, strength in (("one", "strong"), ("two", "weak")):
        article = await _article(session, source.id, suffix)
        await record_suggestions(session, article.id, [{**base, "strength": strength}], entities)
    candidate = (await session.scalars(select(GraphCandidate))).one()
    assert candidate.strength == "weak"


@pytest.mark.asyncio
async def test_single_high_confidence_candidate_can_be_approved(session):
    source = Source(name="Источник", kind="rss")
    first = Entity(name="Ставка", type="macro_indicator")
    second = Entity(name="Банки", type="sector")
    curator = User(username="admin", password_hash="hash", role="admin")
    session.add_all([source, first, second, curator])
    await session.flush()
    article = await _article(session, source.id, "rate")
    entities = {"ставка": first.id, "банки": second.id}
    suggestion = {
        "from_entity": "Ставка",
        "to_entity": "Банки",
        "direction": "negative",
        "strength": "medium",
        "kind": "direct",
        "confidence": 0.92,
        "rationale": "Рост ставки повышает стоимость фондирования",
        "quote": "Ставка выросла",
    }
    await record_suggestions(session, article.id, [suggestion], entities)
    candidate = (await session.scalars(select(GraphCandidate))).one()
    influence = await approve_candidate(session, candidate, curator.id, {})
    await session.commit()

    assert candidate.status == "approved"
    assert candidate.approved_influence_id == influence.id
    stored = await session.get(Influence, influence.id)
    assert stored is not None and stored.created_by == "curator"
    assert stored.source_ref == article.url


@pytest.mark.asyncio
async def test_existing_approved_influence_is_not_suggested_and_rejection_keeps_audit(session):
    source = Source(name="Источник", kind="rss")
    first = Entity(name="Газ", type="commodity")
    second = Entity(name="Энергетика", type="sector")
    curator = User(username="admin", password_hash="hash", role="admin")
    session.add_all([source, first, second, curator])
    await session.flush()
    article = await _article(session, source.id, "gas")
    entities = {"газ": first.id, "энергетика": second.id}
    suggestion = {
        "from_entity": "Газ",
        "to_entity": "Энергетика",
        "direction": "positive",
        "strength": "medium",
        "kind": "direct",
        "confidence": 0.95,
        "rationale": "Газ влияет на сектор",
        "quote": "Газ",
    }
    await record_suggestions(session, article.id, [suggestion], entities)
    candidate = (await session.scalars(select(GraphCandidate))).one()
    await reject_candidate(candidate, curator.id, "Недостаточно экономического смысла")
    await session.commit()
    assert candidate.status == "rejected"
    assert candidate.review_comment.startswith("Недостаточно")

    session.add(
        Influence(
            from_entity_id=first.id,
            to_entity_id=second.id,
            direction="positive",
            is_approved=True,
        )
    )
    await session.flush()
    other = await _article(session, source.id, "gas-other")
    assert await record_suggestions(session, other.id, [suggestion], entities) == 0
