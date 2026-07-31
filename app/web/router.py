from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.connection import get_session
from app.db.models import Article, ArticleEntity, Entity, Security, Source
from app.graph.service import security_entity_ids
from app.strategy.engine import generate_strategy

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


async def _load_news(session: AsyncSession, security_id: int) -> list[dict]:
    target_ids = await security_entity_ids(session, security_id)
    if not target_ids:
        return []

    entities = {e.id: e for e in await session.scalars(select(Entity))}
    mentions = (
        await session.scalars(
            select(ArticleEntity).where(ArticleEntity.entity_id.in_(target_ids))
        )
    ).all()
    article_ids = [m.article_id for m in mentions]
    if not article_ids:
        return []

    articles = (
        await session.scalars(
            select(Article)
            .where(Article.id.in_(article_ids))
            .order_by(Article.published_at.desc())
            .limit(30)
        )
    ).all()
    sources = {s.id: s.name for s in await session.scalars(select(Source))}

    items = []
    for article in articles:
        article_mentions = [
            {
                "name": entities[m.entity_id].name,
                "sentiment": m.sentiment,
                "impact": m.impact,
            }
            for m in mentions
            if m.article_id == article.id and m.entity_id in entities
        ]
        items.append(
            {
                "id": article.id,
                "title": article.title,
                "url": article.url,
                "published_at": article.published_at,
                "source_name": sources.get(article.source_id, ""),
                "text": article.text[:300],
                "entities": article_mentions,
            }
        )
    return items


@router.get("/")
async def index(
    request: Request,
    sector: str = "",
    market: str = "",
    session: AsyncSession = Depends(get_session),
):
    statement = select(Security).order_by(Security.ticker)
    if sector:
        statement = statement.where(Security.sector == sector)
    if market:
        statement = statement.where(Security.market == market)
    securities = (await session.scalars(statement)).all()

    sectors = (
        await session.scalars(
            select(Security.sector).distinct().order_by(Security.sector)
        )
    ).all()
    markets = (
        await session.scalars(
            select(Security.market).distinct().order_by(Security.market)
        )
    ).all()

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "securities": securities,
            "sectors": sectors,
            "markets": markets,
            "current_sector": sector,
            "current_market": market,
        },
    )


@router.get("/securities")
async def search_redirect(request: Request, ticker: str = ""):
    ticker = ticker.strip().upper()
    if ticker:
        return RedirectResponse(url=f"/securities/{ticker}", status_code=307)
    return RedirectResponse(url="/", status_code=307)


@router.get("/securities/{ticker}")
async def security_page(
    request: Request,
    ticker: str,
    session: AsyncSession = Depends(get_session),
):
    security = await session.scalar(
        select(Security).where(Security.ticker == ticker.upper())
    )
    if security is None:
        raise HTTPException(status_code=404, detail="Бумага не найдена")

    result = await generate_strategy(session, security.ticker)
    news = await _load_news(session, security.id)
    return templates.TemplateResponse(
        request,
        "security.html",
        {
            "security": security,
            "strategy": result["strategy"],
            "signals": result["signals"],
            "quotes": result["quotes"],
            "rationale": result["rationale_summary"],
            "news": news,
        },
    )
