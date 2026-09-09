from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    GraphCandidate,
    GraphCandidateEvidence,
    Influence,
    Article,
)

PENDING_CONFIDENCE = 0.70
SINGLE_SOURCE_CONFIDENCE = 0.90
VALID_DIRECTIONS = {"positive", "negative"}
VALID_STRENGTHS = {"weak", "medium", "strong"}
VALID_KINDS = {"direct", "indirect"}


def normalize_suggestion(item: dict, entities: dict[str, int]) -> dict | None:
    from_name = str(item.get("from_entity") or "").strip()
    to_name = str(item.get("to_entity") or "").strip()
    from_id = entities.get(from_name.lower())
    to_id = entities.get(to_name.lower())
    direction = str(item.get("direction") or "").strip()
    strength = str(item.get("strength") or "medium").strip()
    kind = str(item.get("kind") or "direct").strip()
    try:
        confidence = float(item.get("confidence") or 0)
    except (TypeError, ValueError):
        return None
    if (
        from_id is None
        or to_id is None
        or from_id == to_id
        or direction not in VALID_DIRECTIONS
        or strength not in VALID_STRENGTHS
        or kind not in VALID_KINDS
        or not 0 <= confidence <= 1
    ):
        return None
    return {
        "from_entity_id": from_id,
        "to_entity_id": to_id,
        "direction": direction,
        "strength": strength,
        "kind": kind,
        "confidence": confidence,
        "rationale": str(item.get("rationale") or "").strip()[:2000],
        "quote": str(item.get("quote") or "").strip()[:2000],
    }


async def record_suggestions(
    session: AsyncSession,
    article_id: int,
    suggestions: list[dict],
    entities: dict[str, int],
) -> int:
    recorded = 0
    article = await session.get(Article, article_id)
    if article is None:
        return 0
    canonical_article_id = article.cluster_id or article.id
    for raw in suggestions[:3]:
        item = normalize_suggestion(raw, entities)
        if item is None:
            continue
        existing_influence = await session.scalar(
            select(Influence.id).where(
                Influence.from_entity_id == item["from_entity_id"],
                Influence.to_entity_id == item["to_entity_id"],
                Influence.is_approved.is_(True),
            )
        )
        if existing_influence is not None:
            continue
        candidate = await session.scalar(
            select(GraphCandidate).where(
                GraphCandidate.from_entity_id == item["from_entity_id"],
                GraphCandidate.to_entity_id == item["to_entity_id"],
                GraphCandidate.direction == item["direction"],
                GraphCandidate.kind == item["kind"],
                GraphCandidate.status.in_(("needs_evidence", "pending")),
            )
        )
        if candidate is None:
            rejected_with_same_evidence = await session.scalar(
                select(GraphCandidate.id)
                .join(
                    GraphCandidateEvidence,
                    GraphCandidateEvidence.candidate_id == GraphCandidate.id,
                )
                .where(
                    GraphCandidate.from_entity_id == item["from_entity_id"],
                    GraphCandidate.to_entity_id == item["to_entity_id"],
                    GraphCandidate.direction == item["direction"],
                    GraphCandidate.kind == item["kind"],
                    GraphCandidate.status == "rejected",
                    GraphCandidateEvidence.article_id == article_id,
                )
            )
            if rejected_with_same_evidence is not None:
                continue
            candidate = GraphCandidate(
                from_entity_id=item["from_entity_id"],
                to_entity_id=item["to_entity_id"],
                direction=item["direction"],
                strength=item["strength"],
                kind=item["kind"],
                confidence=item["confidence"],
                rationale=item["rationale"],
            )
            session.add(candidate)
            await session.flush()
        already_added = await session.scalar(
            select(GraphCandidateEvidence.id).where(
                GraphCandidateEvidence.candidate_id == candidate.id,
                GraphCandidateEvidence.article_id == article_id,
            )
        )
        if already_added is not None:
            continue
        existing_evidence_articles = (
            await session.scalars(
                select(Article)
                .join(
                    GraphCandidateEvidence,
                    GraphCandidateEvidence.article_id == Article.id,
                )
                .where(GraphCandidateEvidence.candidate_id == candidate.id)
            )
        ).all()
        if any((item.cluster_id or item.id) == canonical_article_id for item in existing_evidence_articles):
            continue
        session.add(
            GraphCandidateEvidence(
                candidate_id=candidate.id,
                article_id=article_id,
                quote=item["quote"],
                rationale=item["rationale"],
                confidence=item["confidence"],
                strength=item["strength"],
            )
        )
        await session.flush()
        await refresh_candidate(session, candidate)
        recorded += 1
    return recorded


async def refresh_candidate(session: AsyncSession, candidate: GraphCandidate) -> None:
    evidence = (
        await session.scalars(
            select(GraphCandidateEvidence).where(
                GraphCandidateEvidence.candidate_id == candidate.id
            )
        )
    ).all()
    if not evidence:
        return
    candidate.evidence_count = len(evidence)
    candidate.confidence = min(
        0.95, sum(item.confidence for item in evidence) / len(evidence)
    )
    # При равном числе подтверждений выбираем более осторожную силу.
    strength_order = {"weak": 0, "medium": 1, "strong": 2}
    strength_counts = {
        strength: sum(item.strength == strength for item in evidence)
        for strength in VALID_STRENGTHS
    }
    candidate.strength = min(
        VALID_STRENGTHS,
        key=lambda strength: (-strength_counts[strength], strength_order[strength]),
    )
    if candidate.evidence_count >= 2 and candidate.confidence >= PENDING_CONFIDENCE:
        candidate.status = "pending"
    elif candidate.evidence_count == 1 and candidate.confidence >= SINGLE_SOURCE_CONFIDENCE:
        candidate.status = "pending"
    else:
        candidate.status = "needs_evidence"
    candidate.updated_at = datetime.now(timezone.utc)


async def approve_candidate(
    session: AsyncSession,
    candidate: GraphCandidate,
    reviewer_id: int,
    values: dict,
) -> Influence:
    if candidate.status in {"approved", "rejected"}:
        raise ValueError("Кандидат уже обработан")
    direction = values.get("direction", candidate.direction)
    strength = values.get("strength", candidate.strength)
    kind = values.get("kind", candidate.kind)
    try:
        confidence = float(values.get("confidence", candidate.confidence))
    except (TypeError, ValueError):
        raise ValueError("Уверенность должна быть от 0 до 1")
    if (
        direction not in VALID_DIRECTIONS
        or strength not in VALID_STRENGTHS
        or kind not in VALID_KINDS
        or not 0 <= confidence <= 1
    ):
        raise ValueError("Недопустимые параметры связи")
    influence = await session.scalar(
        select(Influence).where(
            Influence.from_entity_id == candidate.from_entity_id,
            Influence.to_entity_id == candidate.to_entity_id,
            Influence.is_approved.is_(True),
        )
    )
    if influence is not None:
        raise RuntimeError("Такая утверждённая связь уже существует")
    evidence = (
        await session.scalars(
            select(GraphCandidateEvidence).where(
                GraphCandidateEvidence.candidate_id == candidate.id
            )
        )
    ).all()
    source_ref = str(values.get("source_ref") or "").strip()
    if not source_ref:
        article_ids = [item.article_id for item in evidence]
        articles = (
            await session.scalars(select(Article).where(Article.id.in_(article_ids)))
        ).all()
        source_ref = ",".join(sorted({article.url for article in articles if article.url}))
    if not source_ref:
        source_ref = "curated"
    influence = Influence(
        from_entity_id=candidate.from_entity_id,
        to_entity_id=candidate.to_entity_id,
        direction=direction,
        strength=strength,
        kind=kind,
        confidence=confidence,
        rationale=str(values.get("rationale") or candidate.rationale),
        source_ref=source_ref,
        created_by="curator",
        is_approved=True,
    )
    session.add(influence)
    await session.flush()
    candidate.status = "approved"
    candidate.approved_influence_id = influence.id
    candidate.reviewed_by = reviewer_id
    candidate.reviewed_at = datetime.now(timezone.utc)
    candidate.review_comment = str(values.get("comment") or "")[:2000]
    candidate.updated_at = candidate.reviewed_at
    return influence


async def reject_candidate(
    candidate: GraphCandidate, reviewer_id: int, comment: str = ""
) -> None:
    candidate.status = "rejected"
    candidate.reviewed_by = reviewer_id
    candidate.reviewed_at = datetime.now(timezone.utc)
    candidate.review_comment = comment[:2000]
    candidate.updated_at = candidate.reviewed_at
