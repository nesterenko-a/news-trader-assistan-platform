from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.db.connection import get_session
from app.db.models import (
    Article,
    Entity,
    GraphCandidate,
    GraphCandidateEvidence,
    User,
)
from app.graph.candidates import (
    VALID_DIRECTIONS,
    VALID_KINDS,
    VALID_STRENGTHS,
    approve_candidate,
    reject_candidate,
)

router = APIRouter(prefix="/admin/graph/candidates", tags=["graph-candidates"])


async def _candidate(session: AsyncSession, candidate_id: int) -> GraphCandidate:
    candidate = await session.get(GraphCandidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Кандидат не найден")
    return candidate


async def _candidate_out(session: AsyncSession, candidate: GraphCandidate) -> dict:
    entities = (
        await session.scalars(
            select(Entity).where(Entity.id.in_((candidate.from_entity_id, candidate.to_entity_id)))
        )
    ).all()
    names = {entity.id: entity.name for entity in entities}
    evidence_rows = (
        await session.execute(
            select(GraphCandidateEvidence, Article)
            .join(Article, Article.id == GraphCandidateEvidence.article_id)
            .where(GraphCandidateEvidence.candidate_id == candidate.id)
            .order_by(GraphCandidateEvidence.id.desc())
        )
    ).all()
    return {
        "id": candidate.id,
        "from": names.get(candidate.from_entity_id, "?"),
        "to": names.get(candidate.to_entity_id, "?"),
        "direction": candidate.direction,
        "strength": candidate.strength,
        "kind": candidate.kind,
        "confidence": candidate.confidence,
        "rationale": candidate.rationale,
        "status": candidate.status,
        "evidence_count": candidate.evidence_count,
        "approved_influence_id": candidate.approved_influence_id,
        "review_comment": candidate.review_comment,
        "created_at": candidate.created_at,
        "updated_at": candidate.updated_at,
        "evidence": [
            {
                "article_id": evidence.article_id,
                "title": article.title,
                "url": article.url,
                "published_at": article.published_at,
                "quote": evidence.quote,
                "rationale": evidence.rationale,
                "confidence": evidence.confidence,
            }
            for evidence, article in evidence_rows
        ],
    }


@router.get("")
async def list_candidates(
    status: str | None = "pending",
    entity: str | None = None,
    direction: str | None = None,
    min_confidence: float | None = None,
    page: int = 1,
    per_page: int = 20,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    query = select(GraphCandidate).order_by(GraphCandidate.updated_at.desc(), GraphCandidate.id.desc())
    if status:
        query = query.where(GraphCandidate.status == status)
    if direction in VALID_DIRECTIONS:
        query = query.where(GraphCandidate.direction == direction)
    if min_confidence is not None:
        query = query.where(GraphCandidate.confidence >= min_confidence)
    if entity:
        entity_ids = select(Entity.id).where(Entity.name.ilike(f"%{entity.strip()}%"))
        query = query.where(
            or_(
                GraphCandidate.from_entity_id.in_(entity_ids),
                GraphCandidate.to_entity_id.in_(entity_ids),
            )
        )
    page = max(page, 1)
    per_page = min(max(per_page, 1), 100)
    rows = (await session.scalars(query.offset((page - 1) * per_page).limit(per_page))).all()
    return {"items": [await _candidate_out(session, row) for row in rows], "page": page, "per_page": per_page}


@router.get("/{candidate_id}")
async def get_candidate(
    candidate_id: int,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await _candidate_out(session, await _candidate(session, candidate_id))


@router.post("/{candidate_id}/approve")
async def approve(
    candidate_id: int,
    payload: dict | None = Body(default=None),
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    candidate = await _candidate(session, candidate_id)
    payload = payload or {}
    for field, allowed in (("direction", VALID_DIRECTIONS), ("strength", VALID_STRENGTHS), ("kind", VALID_KINDS)):
        if field in payload and payload[field] not in allowed:
            raise HTTPException(status_code=422, detail=f"Недопустимое значение {field}")
    if "confidence" in payload:
        try:
            if not 0 <= float(payload["confidence"]) <= 1:
                raise ValueError
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="Уверенность должна быть от 0 до 1")
        payload["confidence"] = float(payload["confidence"])
    try:
        influence = await approve_candidate(session, candidate, user.id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await session.commit()
    return {"candidate_id": candidate.id, "influence_id": influence.id, "status": candidate.status}


@router.post("/{candidate_id}/reject")
async def reject(
    candidate_id: int,
    payload: dict | None = Body(default=None),
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    candidate = await _candidate(session, candidate_id)
    payload = payload or {}
    if candidate.status in {"approved", "rejected"}:
        raise HTTPException(status_code=409, detail="Кандидат уже обработан")
    await reject_candidate(candidate, user.id, str(payload.get("comment") or ""))
    await session.commit()
    return {"candidate_id": candidate.id, "status": candidate.status}


@router.post("/{candidate_id}/need-evidence")
async def need_evidence(
    candidate_id: int,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    candidate = await _candidate(session, candidate_id)
    if candidate.status in {"approved", "rejected"}:
        raise HTTPException(status_code=409, detail="Кандидат уже обработан")
    candidate.status = "needs_evidence"
    await session.commit()
    return {"candidate_id": candidate.id, "status": candidate.status}
