import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.connection import Base
from app.db.models import Entity, Influence
from app.graph.service import (
    export_graph_records,
    import_graph_records,
    resolve_entity_id,
    seed_graph,
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


async def _counts(session) -> tuple[int, int]:
    entities = len((await session.scalars(select(Entity))).all())
    influences = len((await session.scalars(select(Influence))).all())
    return entities, influences


async def test_roundtrip_exports_and_imports_full_graph(session):
    """Экспорт графа и импорт в пустую БД воспроизводят все сущности и рёбра без потерь."""
    await seed_graph(session)

    entities, influences = await export_graph_records(session)
    src_e, src_i = len(entities), len(influences)
    assert src_e >= 60 and src_i >= 80

    # Импорт в чистую (незасеянную) БД
    await session.rollback()
    fresh = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with fresh.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(fresh, expire_on_commit=False)
    async with factory() as target:
        counts = await import_graph_records(target, entities, influences)
        await target.commit()
        # созданы все сущности и рёбра
        assert counts["created_e"] == src_e
        assert counts["created_i"] == src_i
        te, ti = await _counts(target)
        assert te == src_e and ti == src_i
        # рёбра ссылаются корректно (чтобы не было сирот с пустыми именами)
        infs = (await target.scalars(select(Influence))).all()
        by_id = {e.id: e.name for e in (await target.scalars(select(Entity))).all()}
        for inf in infs:
            assert by_id[inf.from_entity_id] != ""
            assert by_id[inf.to_entity_id] != ""
    await fresh.dispose()


async def test_import_is_idempotent_over_seeded_graph(session):
    """Повторный импорт поверх уже засеянного графа не создаёт дубликатов."""
    await seed_graph(session)

    entities, influences = await export_graph_records(session)
    # Первый импорт поверх засеянного — ничего нового (всё уже есть)
    counts1 = await import_graph_records(session, entities, influences)
    await session.commit()
    assert counts1["created_i"] == 0
    e1, i1 = await _counts(session)

    # Второй импорт — снова без дублей
    counts2 = await import_graph_records(session, entities, influences)
    await session.commit()
    assert counts2["created_i"] == 0
    e2, i2 = await _counts(session)
    assert (e2, i2) == (e1, i1)


async def test_import_preserves_attrs_and_creates_missing_entities(session):
    """Импорт сохраняет type/aliases/meta/is_approved и создаёт недостающие сущности рёбер."""
    await seed_graph(session)

    entities, influences = await export_graph_records(session)
    # Добавим ребро с недостающей сущностью и нестандартными полями
    influences.append(
        {
            "from": "Новая сущность X",
            "to": "Нефть",
            "direction": "negative",
            "strength": "weak",
            "kind": "indirect",
            "confidence": 0.42,
            "rationale": "тестовое обоснование",
            "source_ref": "https://example.com/x",
            "created_by": "agent",
            "is_approved": False,
        }
    )
    counts = await import_graph_records(session, entities, influences)
    await session.commit()
    assert counts["created_i"] == 1
    assert await resolve_entity_id(session, "Новая сущность X") is not None

    inf = (
        await session.scalars(
            select(Influence).where(Influence.created_by == "agent")
        )
    ).one()
    assert inf.direction == "negative"
    assert inf.strength == "weak"
    assert inf.kind == "indirect"
    assert inf.confidence == 0.42
    assert inf.source_ref == "https://example.com/x"
    assert inf.is_approved is False
