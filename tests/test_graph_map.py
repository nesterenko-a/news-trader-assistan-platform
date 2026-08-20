import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.connection import Base
from app.db.models import Entity, EntityWatchMetric, Security
from app.graph.map import build_dependency_map, layout_dependency_map
from app.graph.seed_data import SECURITIES
from app.graph.service import seed_graph


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as store:
        yield store
    await engine.dispose()


async def _security_id(session, ticker: str) -> int:
    sec = (await session.scalars(select(Security).where(Security.ticker == ticker))).one()
    return sec.id


async def test_build_dependency_map_lukoil(session):
    """Карта для Лукойла включает связанный сектор и предков (Нефть) с механизмом."""
    await seed_graph(session)
    lkoh_id = await _security_id(session, "LKOH")

    # Добавим watch_metric на Нефть
    oil = (await session.scalars(select(Entity).where(Entity.name == "Нефть"))).one()
    session.add(
        EntityWatchMetric(entity_id=oil.id, label="Brent", metric="Марка Brent", sort_order=0)
    )
    await session.commit()

    graph = await build_dependency_map(session, lkoh_id)
    names = {n["name"]: n for n in graph["nodes"]}
    assert names, "карта не пустая"
    # Лукойл или его отраслевой представитель есть
    assert "Лукойл" in names or "Нефтегазовый сектор" in names

    # ребра несут знак и механизм (rationale из графа)
    for e in graph["edges"]:
        assert "from" in e and "to" in e
        assert e["sign"] in (1.0, -1.0)
        assert e["mechanism"] or e["source_ref"]

    # узлы с watch_metric несут metrics
    oil_node = names.get("Нефть")
    if oil_node is not None:
        assert any(m["label"] == "Brent" for m in oil_node["metrics"])


async def test_layout_no_cycles_and_coords(session):
    """Layout даёт координаты в конечной области и не зацикливается на направленных цепочках."""
    await seed_graph(session)
    lkoh_id = await _security_id(session, "LKOH")
    graph = await build_dependency_map(session, lkoh_id)
    coords = layout_dependency_map(graph)
    names = [n["name"] for n in graph["nodes"]]
    assert set(coords.keys()) == set(names)
    # координаты конечные и неотрицательные
    for x, y in coords.values():
        assert x >= 0 and y >= 0
        assert x < 5000 and y < 5000


async def test_build_map_svg(session):
    """SVG-рендер карты не пуст, содержит узлы, стрелки с механизмом и метрики."""
    from app.graph.map_view import build_map_svg

    await seed_graph(session)
    # добавим метрику
    from app.db.models import EntityWatchMetric
    oil = (await session.scalars(select(Entity).where(Entity.name == "Нефть"))).one()
    session.add(EntityWatchMetric(entity_id=oil.id, label="Brent", metric="Марка Brent", sort_order=0))
    await session.commit()

    lkoh_id = await _security_id(session, "LKOH")
    graph = await build_dependency_map(session, lkoh_id)
    svg = build_map_svg(graph)["svg"]
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "depmap" in svg
    # стрелки с механизмом
    assert "dep-mech" in svg
    # если есть стрелки — подпись/знак
    if graph["edges"]:
        assert "+" in svg or "−" in svg


async def test_map_to_cytoscape(session):
    """Преобразование графа в структуру Cytoscape: узлы с для JS, рёбра с sign/label."""
    from app.web.router import _map_to_cytoscape

    await seed_graph(session)
    lkoh_id = await _security_id(session, "LKOH")
    graph = await build_dependency_map(session, lkoh_id)
    cy = _map_to_cytoscape(graph)

    assert cy["nodes"] and cy["edges"]
    # каждый узел несёт id = имя и флаги
    for node in cy["nodes"]:
        d = node["data"]
        assert d["id"] and d["label"]
        assert isinstance(d["is_target"], bool)
        assert isinstance(d["is_key"], bool)
    # рёбра: source/target по именам, sign float, label — механизм (обрезанный)
    for edge in cy["edges"]:
        d = edge["data"]
        assert d["source"] and d["target"]
        assert isinstance(d["sign"], float)
        assert d["label"]
        assert len(d["label"]) <= 61
