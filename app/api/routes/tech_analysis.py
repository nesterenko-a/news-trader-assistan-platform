"""REST API «Теханализ в LLM» (/v1)."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db.connection import SessionLocal, get_session
from app.db.models import TechAnalysis, User
from app.tech_analysis.service import (
    has_active,
    list_analyses,
    retry_analysis,
    start_analysis,
)

router = APIRouter(prefix="/tech-analysis", tags=["tech-analysis"])


async def _get_row(analysis_id: int) -> TechAnalysis:
    async with SessionLocal() as session:
        row = await session.get(TechAnalysis, analysis_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Анализ не найден")
        return row


@router.post("/start")
async def tech_analysis_start(
    ticker: str = "",
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    if not ticker.strip():
        raise HTTPException(status_code=400, detail="Тикер не указан")
    try:
        analysis = await start_analysis(session, ticker, user_id=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"id": analysis.id, "ticker": analysis.ticker, "status": analysis.status}


@router.get("")
async def tech_analysis_list(
    ticker: str,
    page: int = 1,
    _: User = Depends(get_current_user),
) -> dict:
    return await list_analyses(ticker, page=page)


@router.get("/{analysis_id}/status")
async def tech_analysis_status(
    analysis_id: int,
    _: User = Depends(get_current_user),
) -> dict:
    row = await _get_row(analysis_id)
    return {
        "id": row.id,
        "ticker": row.ticker,
        "status": row.status,
        "stage": row.stage,
        "request_ready": bool(row.request_md),
        "response": row.verdict,
        "error": row.error,
    }


@router.post("/{analysis_id}/retry")
async def tech_analysis_retry(
    analysis_id: int,
    _: User = Depends(get_current_user),
) -> dict:
    try:
        row = await retry_analysis(analysis_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Анализ не найден")
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"id": row.id, "status": row.status, "stage": row.stage}


@router.get("/{analysis_id}/request.md")
async def tech_analysis_request_md(
    analysis_id: int,
    _: User = Depends(get_current_user),
) -> Response:
    row = await _get_row(analysis_id)
    filename = f"tech_analysis_{row.ticker}_{analysis_id}_request.md"
    return Response(
        content=row.request_md or "# Запрос ещё не сформирован",
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{analysis_id}/response.md")
async def tech_analysis_response_md(
    analysis_id: int,
    _: User = Depends(get_current_user),
) -> Response:
    row = await _get_row(analysis_id)
    filename = f"tech_analysis_{row.ticker}_{analysis_id}_response.md"
    return Response(
        content=row.response_md or "# Ответ ещё не получен",
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
