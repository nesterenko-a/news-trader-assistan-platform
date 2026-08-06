"""API управления источниками новостей (/v1/sources) — персональные списки."""

import asyncio
import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db.connection import get_session
from app.db.models import Source, User, UserSource
from app.llm.client import LLMClient
from app.news.feed_check import check_feed, validate_feed_url
from app.news.sources_service import (
    SOURCE_CATEGORIES,
    restore_default_sources,
    user_sources,
)
from app.schemas import (
    FeedCandidateOut,
    FeedCheckIn,
    FeedSearchIn,
    RestoreDefaultsOut,
    SourceIn,
    SourceOut,
    SourceUpdateIn,
)

router = APIRouter(prefix="/sources", tags=["sources"])

ALLOWED_KINDS = ("rss",)
SEARCH_CANDIDATES = 8


def _source_out(source: Source) -> dict:
    return {
        "id": source.id,
        "name": source.name,
        "kind": source.kind,
        "url": (source.config or {}).get("url"),
        "category": source.category or "",
        "reputation": source.reputation_score,
        "is_active": source.is_active,
        "last_status": source.last_status,
        "last_error": source.last_error,
        "last_checked_at": source.last_checked_at,
        "use_llm": bool(source.use_llm),
        "use_browser": bool(source.use_browser),
    }


def _valid_category(category: str) -> bool:
    return category in SOURCE_CATEGORIES or category == ""


async def _owned_source(session: AsyncSession, user_id: int, source_id: int) -> Source:
    source = await session.scalar(
        select(Source)
        .join(UserSource, UserSource.source_id == Source.id)
        .where(UserSource.user_id == user_id, Source.id == source_id)
    )
    if source is None:
        raise HTTPException(status_code=404, detail="Источник не найден")
    return source


async def _mark_checked(source: Source, ok: bool, error: str) -> None:
    source.last_status = "ok" if ok else "error"
    source.last_error = "" if ok else error[:500]
    source.last_checked_at = datetime.now(timezone.utc)


@router.get("", response_model=list[SourceOut])
async def list_sources(
    kind: str | None = None,
    category: str | None = None,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    rows = await user_sources(session, user.id, kind=kind, category=category)
    return [_source_out(s) for s in rows]


@router.post("", response_model=SourceOut, status_code=201)
async def add_source(
    payload: SourceIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if payload.kind not in ALLOWED_KINDS:
        raise HTTPException(status_code=400, detail="Недопустимый тип источника")
    if not _valid_category(payload.category):
        raise HTTPException(status_code=400, detail="Недопустимая категория")
    url_error = await validate_feed_url(payload.url)
    if url_error:
        raise HTTPException(status_code=400, detail=url_error)

    url = payload.url.strip()
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Название не может быть пустым")
    source = None
    for cand in await session.scalars(select(Source).where(Source.kind == payload.kind)):
        if (cand.config or {}).get("url") == url:
            source = cand
            break
    if source is None:
        clash = await session.scalar(select(Source).where(Source.name == name))
        if clash is not None:
            raise HTTPException(
                status_code=400,
                detail="Источник с таким именем уже существует с другим URL",
            )
        source = Source(
            name=name,
            kind=payload.kind,
            reputation_score=payload.reputation,
            config={"url": url},
            category=payload.category,
        )
        session.add(source)
        await session.flush()
    else:
        # Тот же URL — тот же источник: обновляем общие метаданные каталога.
        source.config = {**(source.config or {}), "url": url}
        source.category = payload.category
        source.reputation_score = payload.reputation
        await session.flush()

    link = await session.scalar(
        select(UserSource).where(
            UserSource.user_id == user.id, UserSource.source_id == source.id
        )
    )
    if link is None:
        session.add(UserSource(user_id=user.id, source_id=source.id))
    await session.commit()

    ok, error = await check_feed(url)
    await _mark_checked(source, ok, error)
    await session.commit()
    return _source_out(source)


@router.put("/{source_id}", response_model=SourceOut)
async def update_source(
    source_id: int,
    payload: SourceUpdateIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    source = await _owned_source(session, user.id, source_id)
    if payload.category is not None:
        if not _valid_category(payload.category):
            raise HTTPException(status_code=400, detail="Недопустимая категория")
        source.category = payload.category
    if payload.reputation is not None:
        source.reputation_score = payload.reputation
    if payload.is_active is not None:
        source.is_active = payload.is_active
    if payload.use_llm is not None:
        source.use_llm = payload.use_llm
    if payload.use_browser is not None:
        source.use_browser = payload.use_browser
    if payload.name is not None:
        new_name = payload.name.strip()
        if not new_name:
            raise HTTPException(status_code=400, detail="Название не может быть пустым")
        clash = await session.scalar(
            select(Source).where(Source.name == new_name, Source.id != source.id)
        )
        if clash is not None:
            raise HTTPException(status_code=400, detail="Источник с таким именем уже существует")
        source.name = new_name
    if payload.url is not None:
        url_error = await validate_feed_url(payload.url)
        if url_error:
            raise HTTPException(status_code=400, detail=url_error)
        source.config = {**(source.config or {}), "url": payload.url.strip()}
    await session.commit()
    return _source_out(source)


@router.delete("/{source_id}")
async def remove_source(
    source_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    source = await _owned_source(session, user.id, source_id)
    await session.execute(
        UserSource.__table__.delete().where(
            UserSource.user_id == user.id, UserSource.source_id == source.id
        )
    )
    await session.commit()
    return {"status": "ok"}


@router.post("/check", response_model=list[SourceOut])
async def check_sources(
    payload: FeedCheckIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    if payload.ids:
        sources = [
            await _owned_source(session, user.id, sid) for sid in payload.ids
        ]
    else:
        sources = await user_sources(session, user.id, kind="rss")
    urls = [(s, (s.config or {}).get("url") or "") for s in sources]
    results = await asyncio.gather(
        *(
            check_feed(u, use_llm=bool(s.use_llm), use_browser=bool(s.use_browser))
            for s, u in urls
        )
    )
    for (source, _), (ok, error) in zip(urls, results):
        await _mark_checked(source, ok, error)
    await session.commit()
    return [_source_out(s) for s in sources]


@router.post("/search", response_model=list[FeedCandidateOut])
async def search_sources(
    payload: FeedSearchIn,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    if payload.kind not in ALLOWED_KINDS:
        raise HTTPException(status_code=400, detail="Недопустимый тип источника")
    client = LLMClient.from_settings()
    system_prompt = (
        "Ты подбираешь RSS/Atom-ленты новостей по теме. "
        "Верни ТОЛЬКО JSON-массив без пояснений и markdown: "
        '[{"name": "Название", "url": "https://...", "category": "финансы|геополитика|погода|"}] '
        f"({SEARCH_CANDIDATES} кандидатов). URL — полные, реальные, http/https."
    )
    try:
        raw = await client.chat(system_prompt, f"Подбери {SEARCH_CANDIDATES} RSS-лент по теме: {payload.query}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM недоступен: {type(exc).__name__}")

    candidates = _parse_candidates(raw)
    valid = [
        cand
        for cand in candidates[:SEARCH_CANDIDATES]
        if str(cand.get("url", "")).strip().startswith(("http://", "https://"))
    ]
    results = await asyncio.gather(
        *(check_feed(str(cand["url"]).strip()) for cand in valid)
    )
    checked: list[dict] = []
    for cand, (ok, error) in zip(valid, results):
        checked.append(
            {
                "name": str(cand.get("name", cand["url"]))[:200],
                "url": str(cand["url"]).strip(),
                "category": cand.get("category", "") if cand.get("category") in SOURCE_CATEGORIES else "",
                "ok": ok,
                "error": "" if ok else error,
            }
        )
    return checked


def _parse_candidates(raw: str) -> list[dict]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        match = re.search(r"\[.*\]", text, re.S)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, list) else []
        except (ValueError, TypeError):
            return []


@router.post("/restore-defaults", response_model=RestoreDefaultsOut)
async def restore_defaults(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    added = await restore_default_sources(session, user.id)
    return {"added": added}
