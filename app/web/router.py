from pathlib import Path
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.connection import get_session
from app.db.models import Article, ArticleEntity, Entity, MarketCandle, Security, Source
from app.graph.service import security_entity_ids
from app.presentation.factories import WebContextFactory
from app.presentation.view import build_strategy_view
from app.strategy.engine import generate_strategy

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

RANGE_OPTIONS = {"1y": 365, "5y": 5 * 365, "all": None}
MAX_CHART_POINTS = 360

_web_context_factory = WebContextFactory()


def _build_chart(candles: list[MarketCandle], width: int = 900, height: int = 280) -> dict | None:
    valid = [(c.trading_date, c.close) for c in candles if c.close is not None]
    if len(valid) < 2:
        return None

    step = max(1, len(valid) // MAX_CHART_POINTS)
    sampled = valid[::step]

    prices = [close for _, close in sampled]
    min_price = min(prices)
    max_price = max(prices)
    span = max_price - min_price or 1.0
    pad = span * 0.05
    low = min_price - pad
    high = max_price + pad

    points = []
    n = len(sampled)
    for i, (_, close) in enumerate(sampled):
        x = round(10 + i * (width - 20) / (n - 1), 1)
        y = round(10 + (high - close) / (high - low) * (height - 20), 1)
        points.append(f"{x},{y}")

    return {
        "points": " ".join(points),
        "min_price": round(min_price, 2),
        "max_price": round(max_price, 2),
        "first_date": sampled[0][0].isoformat(),
        "last_date": sampled[-1][0].isoformat(),
        "width": width,
        "height": height,
    }


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
    range: str = "1y",
    session: AsyncSession = Depends(get_session),
):
    security = await session.scalar(
        select(Security).where(Security.ticker == ticker.upper())
    )
    if security is None:
        raise HTTPException(status_code=404, detail="Бумага не найдена")

    chart_range = range if range in RANGE_OPTIONS else "1y"
    lookback_days = RANGE_OPTIONS[chart_range]
    statement = (
        select(MarketCandle)
        .where(MarketCandle.security_id == security.id)
        .order_by(MarketCandle.trading_date)
    )
    if lookback_days is not None:
        statement = statement.where(
            MarketCandle.trading_date >= date.today() - timedelta(days=lookback_days)
        )
    candles = (await session.scalars(statement)).all()
    chart = _build_chart(candles)

    result = await generate_strategy(session, security.ticker)
    news = await _load_news(session, security.id)

    view = build_strategy_view(
        security,
        result,
        web_url=f"/securities/{security.ticker}",
    )
    context = _web_context_factory.build(view)
    context.update(
        {
            "quotes": result["quotes"],
            "news": news,
            "chart": chart,
            "chart_range": chart_range,
            "range_options": list(RANGE_OPTIONS.keys()),
        }
    )
    return templates.TemplateResponse(request, "security.html", context)
