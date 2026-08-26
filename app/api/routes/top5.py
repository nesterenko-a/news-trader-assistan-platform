"""REST API «Top-5: лучшая сделка из шаблона акций через Теханализ в LLM» (/v1)."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db.connection import get_session
from app.db.models import User
from app.tech_analysis.batch import (
    batch_progress,
    fresh_analysis,
    get_stock_template,
    start_batch,
    top5,
)

router = APIRouter(prefix="/top5", tags=["top5"])


@router.get("")
async def top5_list(
    template_id: int,
    limit: int = Query(5, ge=1, le=10),
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
) -> dict:
    try:
        return await top5(session, template_id, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/run")
async def top5_run(
    template_id: int,
    provider: str | None = Query(None),
    force: bool = Query(False, description="Принудительно перезапустить Теханализ, игнорируя свежесть анализа"),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    try:
        batch = await start_batch(session, template_id, user_id=user.id, provider=provider, force=force)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"batch_id": batch.id, "template_id": template_id, "status": batch.status, "force": force}


@router.get("/{batch_id}/status")
async def top5_status(
    batch_id: int,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
) -> dict:
    return await batch_progress(session, batch_id)


@router.get("/{batch_id}")
async def top5_batch_detail(
    batch_id: int,
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
) -> dict:
    from sqlalchemy import select

    from app.db.models import TechAnalysis, TechAnalysisBatch

    batch = await session.get(TechAnalysisBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Батч не найден")
    rows = await session.scalars(
        select(TechAnalysis).where(TechAnalysis.batch_id == batch_id).order_by(TechAnalysis.ticker)
    )
    items = [
        {
            "ticker": r.ticker,
            "status": r.status,
            "stage": r.stage,
            "analysis_id": r.id,
            "verdict": r.verdict,
        }
        for r in rows.all()
    ]
    return {"batch_id": batch_id, "status": batch.status, "items": items}
