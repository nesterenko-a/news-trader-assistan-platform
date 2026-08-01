from pathlib import Path
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.service import get_settings, load_alerts, mark_all_read, mark_read, update_settings
from app.auth import (
    create_session,
    delete_session,
    get_current_user,
    hash_password,
    verify_password,
)
from app.db.connection import get_session
from app.db.models import (
    MarketCandle,
    PortfolioPosition,
    Security,
    Strategy,
    User,
    WatchlistItem,
)
from app.market.moex import MOEXClient
from app.news.service import load_security_news
from app.presentation.factories import WebContextFactory
from app.presentation.view import build_strategy_view
from app.macro.service import event_tickers, list_events, list_security_events
from app.strategy.engine import generate_strategy

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

RANGE_OPTIONS = {"1y": 365, "5y": 5 * 365, "all": None}
MAX_CHART_POINTS = 360

_web_context_factory = WebContextFactory()
_moex = MOEXClient()


async def _optional_user(
    request: Request, session: AsyncSession
) -> User | None:
    try:
        return await get_current_user(request, session)
    except HTTPException:
        return None


def _base_context(user: User | None) -> dict:
    return {"user": user}


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


@router.get("/")
async def index(
    request: Request,
    sector: str = "",
    market: str = "",
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
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

    context = _base_context(user)
    context.update(
        {
            "securities": securities,
            "sectors": sectors,
            "markets": markets,
            "current_sector": sector,
            "current_market": market,
        }
    )
    return templates.TemplateResponse(request, "index.html", context)


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
    user = await _optional_user(request, session)
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
    news = await load_security_news(session, security.id)

    view = build_strategy_view(
        security,
        result,
        web_url=f"/securities/{security.ticker}",
        news=news,
    )
    context = _web_context_factory.build(view)
    macro_rows = await list_security_events(session, security.id)
    macro_items = [
        {
            "title": event.title,
            "date_str": event.event_time.strftime("%d.%m.%Y %H:%M"),
            "impact": event.expected_impact,
            "region": event.region,
            "tickers": tickers,
        }
        for event, tickers in macro_rows
    ]
    context.update(
        {
            "user": user,
            "quotes": result["quotes"],
            "chart": chart,
            "chart_range": chart_range,
            "range_options": list(RANGE_OPTIONS.keys()),
            "macro_events": macro_items,
        }
    )
    return templates.TemplateResponse(request, "security.html", context)


@router.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": ""})


@router.post("/login")
async def login_post(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    form = await request.form()
    username = str(form.get("username") or "").strip()
    password = str(form.get("password") or "")
    user = await session.scalar(select(User).where(User.username == username))
    if user is None or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Неверное имя пользователя или пароль"}
        )
    token = await create_session(session, user)
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie("nt_token", token, httponly=True)
    return response


@router.get("/register")
async def register_page(request: Request):
    return templates.TemplateResponse(request, "register.html", {"error": ""})


@router.post("/register")
async def register_post(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    form = await request.form()
    username = str(form.get("username") or "").strip()
    password = str(form.get("password") or "")
    if len(username) < 3 or len(password) < 6:
        return templates.TemplateResponse(
            request,
            "register.html",
            {"error": "Имя минимум 3 символа, пароль минимум 6"},
        )
    existing = await session.scalar(select(User).where(User.username == username))
    if existing is not None:
        return templates.TemplateResponse(
            request, "register.html", {"error": "Пользователь уже существует"}
        )
    user = User(username=username, password_hash=hash_password(password))
    session.add(user)
    await session.flush()
    token = await create_session(session, user)
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie("nt_token", token, httponly=True)
    return response


@router.get("/logout")
async def logout_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    token = request.cookies.get("nt_token")
    if token:
        await delete_session(session, token)
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("nt_token")
    return response


@router.get("/watchlist")
async def watchlist_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    rows = (
        await session.execute(
            select(WatchlistItem, Security)
            .join(Security, Security.id == WatchlistItem.security_id)
            .where(WatchlistItem.user_id == user.id)
            .order_by(WatchlistItem.created_at)
        )
    ).all()
    items = []
    for item, security in rows:
        strategy = await session.scalar(
            select(Strategy)
            .where(Strategy.security_id == security.id)
            .order_by(Strategy.generated_at.desc())
            .limit(1)
        )
        items.append(
            {
                "ticker": security.ticker,
                "name": security.name,
                "sector": security.sector,
                "verdict": strategy.verdict if strategy else None,
                "confidence": strategy.confidence if strategy else None,
            }
        )
    context = _base_context(user)
    context.update({"items": items})
    return templates.TemplateResponse(request, "watchlist.html", context)


@router.get("/portfolio")
async def portfolio_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    rows = (
        await session.execute(
            select(PortfolioPosition, Security)
            .join(Security, Security.id == PortfolioPosition.security_id)
            .where(PortfolioPosition.user_id == user.id)
            .order_by(PortfolioPosition.opened_at)
        )
    ).all()
    positions = []
    total_value = 0.0
    total_cost = 0.0
    for position, security in rows:
        quote = await _moex.fetch_quote(security.ticker)
        price = quote["price"] if quote else None
        strategy = await session.scalar(
            select(Strategy)
            .where(Strategy.security_id == security.id)
            .order_by(Strategy.generated_at.desc())
            .limit(1)
        )
        cost_basis = position.quantity * position.avg_price
        market_value = position.quantity * price if price else None
        pnl = market_value - cost_basis if market_value is not None else None
        pnl_percent = (market_value / cost_basis - 1) * 100 if market_value and cost_basis else None
        if market_value is not None:
            total_value += market_value
        total_cost += cost_basis
        positions.append(
            {
                "ticker": security.ticker,
                "name": security.name,
                "sector": security.sector,
                "quantity": position.quantity,
                "avg_price": position.avg_price,
                "current_price": price,
                "market_value": market_value,
                "cost_basis": cost_basis,
                "pnl": pnl,
                "pnl_percent": pnl_percent,
                "verdict": strategy.verdict if strategy else None,
            }
        )
    total_pnl = total_value - total_cost
    context = _base_context(user)
    context.update(
        {
            "positions": positions,
            "total_value": total_value,
            "total_cost": total_cost,
            "total_pnl": total_pnl,
        }
    )
    return templates.TemplateResponse(request, "portfolio.html", context)


@router.get("/alerts")
async def alerts_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    alerts = await load_alerts(session, user.id)
    tickers = {}
    if alerts:
        rows = await session.scalars(
            select(Security).where(Security.id.in_({a.security_id for a in alerts}))
        )
        tickers = {s.id: s.ticker for s in rows.all()}
    settings = await get_settings(session, user.id)
    items = [
        {
            "id": a.id,
            "ticker": tickers.get(a.security_id, ""),
            "headline": a.headline,
            "url": a.url,
            "impact": a.impact,
            "is_ambiguous": a.is_ambiguous,
            "is_read": a.is_read,
            "created_at": a.created_at,
        }
        for a in alerts
    ]
    context = _base_context(user)
    context.update(
        {"items": items, "settings": settings, "unread": sum(1 for i in items if not i["is_read"])}
    )
    return templates.TemplateResponse(request, "alerts.html", context)


@router.post("/api-alerts-read")
async def alerts_read_one(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    form = await request.form()
    try:
        alert_id = int(str(form.get("alert_id") or "0"))
    except ValueError:
        alert_id = 0
    if alert_id:
        await mark_read(session, user.id, alert_id)
    return RedirectResponse(url="/alerts", status_code=303)


@router.post("/api-alerts-read-all")
async def alerts_read_all(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    await mark_all_read(session, user.id)
    return RedirectResponse(url="/alerts", status_code=303)


@router.post("/api-alerts-settings")
async def alerts_settings_post(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    form = await request.form()
    try:
        min_impact = float(str(form.get("min_impact") or "0.7"))
    except ValueError:
        min_impact = 0.7
    channels = []
    if form.get("channel_app") == "on":
        channels.append("app")
    if form.get("channel_telegram") == "on":
        channels.append("telegram")
    await update_settings(session, user.id, min_impact=min_impact, channels=channels)
    return RedirectResponse(url="/alerts", status_code=303)


@router.get("/macro")
async def macro_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    events = await list_events(session)
    impact_order = {"high": 0, "medium": 1, "low": 2}
    events = sorted(events, key=lambda e: (e.event_time, impact_order.get(e.expected_impact, 3)))
    items = []
    for event in events:
        item = {
            "title": event.title,
            "event_time": event.event_time,
            "date_str": event.event_time.strftime("%d.%m.%Y %H:%M"),
            "region": event.region,
            "impact": event.expected_impact,
            "market_wide": event.market_wide,
            "description": event.description,
            "tickers": (
                await event_tickers(session, event.id) if not event.market_wide else []
            ),
        }
        items.append(item)
    watchlist_tickers: list[str] = []
    portfolio_tickers: list[str] = []
    if user is not None:
        watchlist_tickers = list(
            (
                await session.scalars(
                    select(Security.ticker)
                    .join(WatchlistItem, WatchlistItem.security_id == Security.id)
                    .where(WatchlistItem.user_id == user.id)
                    .order_by(Security.ticker)
                )
            ).all()
        )
        portfolio_tickers = list(
            (
                await session.scalars(
                    select(Security.ticker)
                    .join(PortfolioPosition, PortfolioPosition.security_id == Security.id)
                    .where(PortfolioPosition.user_id == user.id)
                    .order_by(Security.ticker)
                )
            ).all()
        )
    context = _base_context(user)
    context.update(
        {
            "items": items,
            "regions": sorted({event.region for event in events}),
            "watchlist_tickers": watchlist_tickers,
            "portfolio_tickers": portfolio_tickers,
        }
    )
    return templates.TemplateResponse(request, "macro.html", context)


@router.post("/api-watchlist-add")
async def watchlist_add(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    form = await request.form()
    ticker = str(form.get("ticker") or "").strip().upper()
    if ticker:
        security = await session.scalar(select(Security).where(Security.ticker == ticker))
        if security is not None:
            existing = await session.scalar(
                select(WatchlistItem).where(
                    WatchlistItem.user_id == user.id, WatchlistItem.security_id == security.id
                )
            )
            if existing is None:
                session.add(WatchlistItem(user_id=user.id, security_id=security.id))
                await session.commit()
    return RedirectResponse(url="/watchlist", status_code=303)


@router.post("/api-watchlist-remove")
async def watchlist_remove(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    form = await request.form()
    ticker = str(form.get("ticker") or "").strip().upper()
    if ticker:
        security = await session.scalar(select(Security).where(Security.ticker == ticker))
        if security is not None:
            await session.execute(
                delete(WatchlistItem).where(
                    WatchlistItem.user_id == user.id, WatchlistItem.security_id == security.id
                )
            )
            await session.commit()
    return RedirectResponse(url="/watchlist", status_code=303)


@router.post("/api-portfolio-add")
async def portfolio_add(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    form = await request.form()
    ticker = str(form.get("ticker") or "").strip().upper()
    try:
        quantity = float(str(form.get("quantity") or "0"))
        avg_price = float(str(form.get("avg_price") or "0"))
    except ValueError:
        quantity, avg_price = 0.0, 0.0
    if ticker and quantity > 0 and avg_price > 0:
        security = await session.scalar(select(Security).where(Security.ticker == ticker))
        if security is not None:
            existing = await session.scalar(
                select(PortfolioPosition).where(
                    PortfolioPosition.user_id == user.id,
                    PortfolioPosition.security_id == security.id,
                )
            )
            if existing is None:
                session.add(
                    PortfolioPosition(
                        user_id=user.id,
                        security_id=security.id,
                        quantity=quantity,
                        avg_price=avg_price,
                    )
                )
                await session.commit()
    return RedirectResponse(url="/portfolio", status_code=303)


@router.post("/api-portfolio-remove")
async def portfolio_remove(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    form = await request.form()
    ticker = str(form.get("ticker") or "").strip().upper()
    if ticker:
        security = await session.scalar(select(Security).where(Security.ticker == ticker))
        if security is not None:
            await session.execute(
                delete(PortfolioPosition).where(
                    PortfolioPosition.user_id == user.id,
                    PortfolioPosition.security_id == security.id,
                )
            )
            await session.commit()
    return RedirectResponse(url="/portfolio", status_code=303)
