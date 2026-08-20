from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Entity, EntityWatchMetric, Influence
from app.graph.service import security_entity_ids

DIRECTION_SIGN = {"positive": 1.0, "negative": -1.0}
KEY_TYPES = {"commodity", "macro_indicator", "currency", "index", "event"}


async def build_dependency_map(
    session: AsyncSession,
    security_id: int,
    max_hops: int = 2,
) -> dict:
    """Построить карту зависимостей вокруг ценной бумаги.

    Возвращает {'nodes': [...], 'edges': [...]}, где:
    - node: {id, name, type, is_key, metrics: [expr {label, metric}], is_target}
    - edge: {from, to, sign, direction, strength, kind, mechanism, source_ref}
    Подграф собирается из рёбер knowledge graph (is_approved), начиная с сущностей,
    связанных с бумагой, и их предков (причин) не глубже max_hops — так карта
    показывает цепочку «причина → ... → сектор → бумага».
    """
    target_ids = set(await security_entity_ids(session, security_id))
    if not target_ids:
        return {"nodes": [], "edges": []}

    rows = (await session.scalars(select(Influence).where(Influence.is_approved.is_(True)))).all()
    by_target = {}
    for edge in rows:
        by_target.setdefault(edge.to_entity_id, []).append(edge)

    # Собрать предков (причины) для целевых узлов с учётом глубины.
    ancestors = {}
    frontier = {n for n in target_ids}
    for _ in range(max_hops):
        next_frontier = set()
        for node in frontier:
            for edge in by_target.get(node, []):
                if edge.from_entity_id in ancestors or edge.from_entity_id in target_ids:
                    continue
                ancestors.setdefault(edge.from_entity_id, node)
                next_frontier.add(edge.from_entity_id)
        if not next_frontier:
            break
        frontier = next_frontier

    selected = set(target_ids) | set(ancestors.keys())

    entities = {
        e.id: e
        for e in (await session.scalars(select(Entity).where(Entity.id.in_(selected)))).all()
    }

    # Матреки «что отслеживать»
    metrics_rows = (
        await session.scalars(
            select(EntityWatchMetric)
            .where(EntityWatchMetric.entity_id.in_(selected))
            .order_by(EntityWatchMetric.entity_id, EntityWatchMetric.sort_order)
        )
    ).all()
    metrics_by_entity: dict[int, list[dict]] = {}
    for m in metrics_rows:
        metrics_by_entity.setdefault(m.entity_id, []).append(
            {"label": m.label, "metric": m.metric}
        )

    nodes = []
    for eid in sorted(selected):
        ent = entities[eid]
        nodes.append(
            {
                "id": eid,
                "name": ent.name,
                "type": ent.type,
                "is_key": ent.type in KEY_TYPES,
                "is_target": eid in target_ids,
                "metrics": metrics_by_entity.get(eid, []),
            }
        )

    # Рёбра — только между выбранными узлами
    edges = []
    seen = set()
    for edge in rows:
        if edge.from_entity_id not in selected or edge.to_entity_id not in selected:
            continue
        key = (edge.from_entity_id, edge.to_entity_id)
        if key in seen:
            continue
        seen.add(key)
        edges.append(
            {
                "from": entities[edge.from_entity_id].name,
                "to": entities[edge.to_entity_id].name,
                "from_id": edge.from_entity_id,
                "to_id": edge.to_entity_id,
                "sign": DIRECTION_SIGN.get(edge.direction, 1.0),
                "direction": edge.direction,
                "strength": edge.strength,
                "kind": edge.kind,
                "confidence": edge.confidence,
                "mechanism": edge.rationale or "",
                "source_ref": edge.source_ref,
            }
        )

    return {"nodes": nodes, "edges": edges}


def layout_dependency_map(graph: dict, x_gap: int = 220, y_gap: int = 90) -> dict:
    """Вычислить координаты узлов для SVG-раскладки (слои «причина → следствие»).

    Возвращает {name: (x, y)}. Узлы без входящих рёбер — левый слой; уровень
    узла = max(уровни источников входящих рёбер) + 1 (долгий путь). Внутри слоя
    узлы распределяются по вертикали.
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    names = [n["name"] for n in nodes]
    in_degree: dict[str, int] = {name: 0 for name in names}
    out_adj: dict[str, list[str]] = {name: [] for name in names}
    for e in edges:
        if e["from"] in out_adj and e["to"] in out_adj:
            out_adj[e["from"]].append(e["to"])
            in_degree[e["to"]] += 1

    # Топологический уровень: итеративно — узел переходит на уровень на 1 больше,
    # чем максимум среди уже вычисленных предшественников (долгий путь).
    level: dict[str, int] = {name: 0 for name in names}
    changed = True
    while changed:
        changed = False
        for name in names:
            pred_levels = []
            for f, tos in out_adj.items():
                if name in tos:
                    pred_levels.append(level[f] + 1)
            if pred_levels:
                target = max(pred_levels)
                if target > level[name]:
                    level[name] = target
                    changed = True

    # распределение по слоям
    layers: dict[int, list[str]] = {}
    for name in names:
        layers.setdefault(level[name], []).append(name)

    coords: dict[str, tuple[int, int]] = {}
    for lvl, layer_names in layers.items():
        for i, name in enumerate(layer_names):
            coords[name] = (lvl * x_gap, i * y_gap + 60)
    return coords

