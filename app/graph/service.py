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


async def add_influence_with_source(
    session: AsyncSession,
    from_name: str,
    to_name: str,
    url: str,
    rationale: str = "",
    strength: str = "medium",
    confidence: float = 0.7,
    direction: str = "positive",
    kind: str = "direct",
) -> dict:
    """Добавить/дополнить ребро графа научной/аналитической ссылкой (FR-05-08).

    - Ребро `from → to` нацеливается по именам сущностей (entities.name уникален).
    - Если ребро уже существует — в его `source_ref` добавляется ссылка
      (без дублей); иначе создаётся новое ребро.
    - Если сущности `from`/`to` нет — создаётся автоматически (type выбирается
      по best-effort из известных; при неизвестном — default).
    Возвращает {"status": "created"|"updated"|"duplicate", "influence_id": int}.
    """
    from_entity, from_created = await _get_or_create_entity(session, from_name)
    to_entity, to_created = await _get_or_create_entity(session, to_name)

    influence = await session.scalar(
        select(Influence).where(
            Influence.from_entity_id == from_entity.id,
            Influence.to_entity_id == to_entity.id,
        )
    )

    if influence is None:
        influence = Influence(
            from_entity_id=from_entity.id,
            to_entity_id=to_entity.id,
            direction=direction,
            strength=strength,
            kind=kind,
            confidence=confidence,
            rationale=rationale,
            source_ref=url,
            created_by="curator",
        )
        session.add(influence)
        await session.flush()
        return {"status": "created", "influence_id": influence.id}

    # Существующее ребро: дополнить source_ref без дублей.
    existing = [s.strip() for s in (influence.source_ref or "").split(",") if s.strip()]
    if url in existing:
        await session.flush()
        return {"status": "duplicate", "influence_id": influence.id}
    existing.append(url)
    influence.source_ref = ",".join(existing)
    if not influence.rationale and rationale:
        influence.rationale = rationale
    await session.flush()
    return {"status": "updated", "influence_id": influence.id}


async def _get_or_create_entity(
    session: AsyncSession, name: str
) -> tuple[Entity, bool]:
    name = name.strip()
    entity_id = await resolve_entity_id(session, name)
    if entity_id is not None:
        entity = await session.get(Entity, entity_id)
        return entity, False
    entity = Entity(
        name=name,
        type="sector",  # best-effort; уточняется при расширении
        aliases=[],
    )
    session.add(entity)
    await session.flush()
    return entity, True


async def export_graph_records(session: AsyncSession) -> tuple[list[dict], list[dict]]:
    """Собрать все сущности и рёбра графа как списки dict-записей (для переноса/резервной копии).

    Возвращает (entities, influences), где influences ссылаются на сущности по имени,
    что делает дамп переносимым между БД.
    """
    entities = (await session.scalars(select(Entity))).all()
    influences = (await session.scalars(select(Influence))).all()
    names = {e.id: e.name for e in entities}
    e_records = [
        {
            "name": e.name,
            "type": e.type,
            "aliases": e.aliases or [],
            "meta": e.meta or {},
        }
        for e in entities
    ]
    i_records = [
        {
            "from": names.get(i.from_entity_id, ""),
            "to": names.get(i.to_entity_id, ""),
            "direction": i.direction,
            "strength": i.strength,
            "kind": i.kind,
            "confidence": i.confidence,
            "rationale": i.rationale or "",
            "source_ref": i.source_ref or "",
            "created_by": i.created_by,
            "is_approved": i.is_approved,
        }
        for i in influences
    ]
    return e_records, i_records


async def import_graph_records(
    session: AsyncSession,
    entities: list[dict],
    influences: list[dict],
) -> dict[str, int]:
    """Идемпотентный импорт записей графа (формат export_graph_records).

    Сущности создаются/дополняются по имени (type/aliases/meta), рёбра —
    по паре (from_name, to_name, created_by). Существующие рёбра не дублируются,
    при этом source_ref безопасно дополняется без повторов.
    Возвращает счётчики: created_e, touched_e, created_i, skipped_i, merged_i.
    """
    created_e = touched_e = created_i = skipped_i = merged_i = 0

    for rec in entities:
        name = (rec.get("name") or "").strip()
        if not name:
            continue
        entity = await session.scalar(select(Entity).where(Entity.name == name))
        if entity is None:
            entity = Entity(
                name=name,
                type=(rec.get("type") or "sector"),
                aliases=rec.get("aliases") or [],
                meta=rec.get("meta") or {},
            )
            session.add(entity)
            await session.flush()
            created_e += 1
        else:
            changed = False
            if not entity.type and rec.get("type"):
                entity.type = rec["type"]
                changed = True
            if not entity.aliases and rec.get("aliases"):
                entity.aliases = rec["aliases"]
                changed = True
            if not entity.meta and rec.get("meta"):
                entity.meta = rec["meta"]
                changed = True
            if changed:
                touched_e += 1

    for rec in influences:
        from_name = (rec.get("from") or "").strip()
        to_name = (rec.get("to") or "").strip()
        if not from_name or not to_name:
            continue

        from_entity = await _entity_by_id_or_name(session, from_name)
        if from_entity is None:
            from_entity = Entity(name=from_name, type="sector", aliases=[], meta={})
            session.add(from_entity)
            await session.flush()
        to_entity = await _entity_by_id_or_name(session, to_name)
        if to_entity is None:
            to_entity = Entity(name=to_name, type="sector", aliases=[], meta={})
            session.add(to_entity)
            await session.flush()

        created_by = rec.get("created_by") or "curator"
        existing = await session.scalar(
            select(Influence).where(
                Influence.from_entity_id == from_entity.id,
                Influence.to_entity_id == to_entity.id,
                Influence.created_by == created_by,
            )
        )
        if existing is not None:
            new_ref = (rec.get("source_ref") or "").strip()
            if new_ref and new_ref != "curated":
                cur = [s.strip() for s in (existing.source_ref or "").split(",") if s.strip()]
                merged = False
                for s in new_ref.split(","):
                    s = s.strip()
                    if s and s not in cur:
                        cur.append(s)
                        merged = True
                if merged:
                    existing.source_ref = ",".join(cur)
                    merged_i += 1
                else:
                    skipped_i += 1
            else:
                skipped_i += 1
            continue

        influence = Influence(
            from_entity_id=from_entity.id,
            to_entity_id=to_entity.id,
            direction=(rec.get("direction") or "positive")[:10],
            strength=(rec.get("strength") or "medium")[:10],
            kind=(rec.get("kind") or "direct")[:10],
            confidence=float(rec.get("confidence") or 0.5),
            rationale=rec.get("rationale") or "",
            source_ref=(rec.get("source_ref") or "").strip(),
            created_by=(created_by or "curator")[:20],
            is_approved=bool(rec.get("is_approved", True)),
        )
        session.add(influence)
        created_i += 1

    return {
        "created_e": created_e,
        "touched_e": touched_e,
        "created_i": created_i,
        "skipped_i": skipped_i,
        "merged_i": merged_i,
    }


async def _entity_by_id_or_name(session: AsyncSession, name: str) -> Entity | None:
    if not name.strip():
        return None
    mapped = await load_entities(session)
    return mapped.get(name.strip()) or mapped.get(name.strip().lower())
