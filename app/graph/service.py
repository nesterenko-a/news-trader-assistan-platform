from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Entity, Influence, Security, security_entity
from app.graph.seed_data import ENTITIES, INFLUENCES, SECURITIES

STRENGTH_WEIGHT = {"weak": 1, "medium": 2, "strong": 3}
DIRECTION_SIGN = {"positive": 1.0, "negative": -1.0}
HOP_DECAY = 0.8


@dataclass
class InfluencePath:
    entities: list[str]
    sign: float
    strength: float
    confidence: float
    source_ref: str = ""


async def seed_graph(session: AsyncSession) -> None:
    entity_ids = {}
    for item in ENTITIES:
        entity = await session.scalar(select(Entity).where(Entity.name == item["name"]))
        if entity is None:
            entity = Entity(
                name=item["name"],
                type=item["type"],
                aliases=item["aliases"],
            )
            session.add(entity)
            await session.flush()
        entity_ids[item["name"]] = entity.id

    for item in INFLUENCES:
        exists = await session.scalar(
            select(Influence).where(
                Influence.from_entity_id == entity_ids[item["from"]],
                Influence.to_entity_id == entity_ids[item["to"]],
                Influence.created_by == "curator",
            )
        )
        if exists is None:
            session.add(
                Influence(
                    from_entity_id=entity_ids[item["from"]],
                    to_entity_id=entity_ids[item["to"]],
                    direction=item["direction"],
                    strength=item["strength"],
                    kind=item["kind"],
                    confidence=item["confidence"],
                    rationale=item["rationale"],
                    source_ref=item["source_ref"],
                    created_by="curator",
                )
            )

    security_ids = []
    for item in SECURITIES:
        security = await session.scalar(
            select(Security).where(Security.ticker == item["ticker"])
        )
        if security is None:
            security = Security(
                ticker=item["ticker"],
                name=item["name"],
                market=item["market"],
                security_type=item["security_type"],
                sector=item["sector"],
                currency=item["currency"],
                aliases=item["aliases"],
            )
            session.add(security)
            await session.flush()

        entity_name = item["name"]
        if entity_name in entity_ids:
            linked = await session.scalar(
                select(security_entity).where(
                    security_entity.c.security_id == security.id,
                    security_entity.c.entity_id == entity_ids[entity_name],
                )
            )
            if linked is None:
                await session.execute(
                    security_entity.insert().values(
                        security_id=security.id,
                        entity_id=entity_ids[entity_name],
                    )
                )
        security_ids.append(security.id)

    await session.commit()


async def load_entities(session: AsyncSession) -> dict[str, Entity]:
    rows = await session.scalars(select(Entity))
    mapping: dict[str, Entity] = {}
    for e in rows:
        mapping[e.name] = e
        mapping[e.name.lower()] = e
        for alias in e.aliases or []:
            mapping[alias] = e
            mapping[alias.lower()] = e
    return mapping


async def resolve_entity_id(session: AsyncSession, name_or_alias: str) -> int | None:
    entities = await load_entities(session)
    entity = entities.get(name_or_alias.strip()) or entities.get(name_or_alias.strip().lower())
    return entity.id if entity else None


async def find_influence_paths(
    session: AsyncSession,
    start_entity_id: int,
    target_entity_id: int,
    max_depth: int = 3,
) -> list[InfluencePath]:
    rows = await session.scalars(
        select(Influence).where(Influence.is_approved.is_(True))
    )
    edges = {}
    entity_names = {}
    for e in await session.scalars(select(Entity)):
        entity_names[e.id] = e.name
    for edge in rows:
        edges.setdefault(edge.from_entity_id, []).append(edge)

    paths = []
    queue = [(start_entity_id, [start_entity_id], 1.0, 1.0, 1.0, [])]

    while queue:
        current, visited, sign, strength, confidence, sources = queue.pop(0)
        if current == target_entity_id and len(visited) > 1:
            # Наиболее значимая непустая ссылка обоснования вдоль цепочки;
            # если таких нет — остаётся пустая (курируемая без документа).
            source_ref = next((s for _, s in sources if s and s != "curated"), "")
            paths.append(
                InfluencePath(
                    entities=[entity_names[i] for i in visited],
                    sign=sign,
                    strength=strength,
                    confidence=confidence,
                    source_ref=source_ref,
                )
            )
            continue
        if len(visited) > max_depth:
            continue
        for edge in edges.get(current, []):
            if edge.to_entity_id in visited:
                continue
            next_sign = sign * DIRECTION_SIGN.get(edge.direction, 1.0)
            next_strength = min(strength, STRENGTH_WEIGHT.get(edge.strength, 2)) * HOP_DECAY
            next_confidence = confidence * edge.confidence
            queue.append(
                (
                    edge.to_entity_id,
                    visited + [edge.to_entity_id],
                    next_sign,
                    next_strength,
                    next_confidence,
                    sources + [(edge.id, edge.source_ref)],
                )
            )

    paths.sort(key=lambda p: (abs(p.sign) * p.strength * p.confidence, len(p.entities)), reverse=True)
    return paths[:10]


async def security_entity_ids(session: AsyncSession, security_id: int) -> list[int]:
    rows = await session.execute(
        select(security_entity.c.entity_id).where(
            security_entity.c.security_id == security_id
        )
    )
    return [r[0] for r in rows]
