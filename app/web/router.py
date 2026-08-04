from pathlib import Path
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.service import (
    get_settings,
    load_alerts,
    mark_all_read,
    mark_read,
    unread_count,
    update_settings,
)
from app.auth import (
    create_session,
    delete_session,
    get_current_user,
    hash_password,
    verify_password,
)
from app.bot.linking import consume_link_code, set_user_chat, unlink_telegram
from app.bot.push import get_bot_username
from app.admin.runner import SCRIPTS, get_script, is_busy, launch
from app.feedback.service import (
    get_rating,
    ratings_map,
    record_feedback_for_security,
    set_feedback,
    user_stats,
)
from app.paper.service import (
    account_view,
    close_position,
    get_or_create_account,
    latest_closes,
    reset_account,
)
from app.notices.service import notice_state
from app.db.connection import get_session
from app.db.models import (
    MarketCandle,
    PortfolioPosition,
    ScriptRun,
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


async def _base_context(session: AsyncSession, user: User | None) -> dict:
    unread_alerts = await unread_count(session, user.id) if user is not None else 0
    return {
        "user": user,
        "is_admin": bool(user is not None and user.role == "admin"),
        "unread_alerts": unread_alerts,
    }


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

    context = await _base_context(session, user)
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
    last_strategy = await session.scalar(
        select(Strategy)
        .where(Strategy.security_id == security.id)
        .order_by(Strategy.generated_at.desc(), Strategy.id.desc())
        .limit(1)
    )
    strategy_id = last_strategy.id if last_strategy else None
    my_rating = (
        await get_rating(session, strategy_id, user.id)
        if strategy_id is not None and user is not None
        else None
    )
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
            "strategy_id": strategy_id,
            "my_rating": my_rating,
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
    context = await _base_context(session, user)
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
    context = await _base_context(session, user)
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
    context = await _base_context(session, user)
    bot_username = await get_bot_username()
    context.update(
        {
            "items": items,
            "settings": settings,
            "unread": sum(1 for i in items if not i["is_read"]),
            "telegram_chat_id": user.telegram_chat_id,
            "bot_username": bot_username,
            "tg_error": request.query_params.get("tg_error") == "1",
        }
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


@router.post("/api-alerts-telegram-link")
async def alerts_telegram_link(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    form = await request.form()
    code = str(form.get("code") or "").strip()
    chat_id = await consume_link_code(session, code) if code else None
    if chat_id is None:
        return RedirectResponse(url="/alerts?tg_error=1", status_code=303)
    await set_user_chat(session, user.id, chat_id)
    return RedirectResponse(url="/alerts", status_code=303)


@router.post("/api-alerts-telegram-unlink")
async def alerts_telegram_unlink(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    await unlink_telegram(session, user.id)
    return RedirectResponse(url="/alerts", status_code=303)


@router.get("/history")
async def history_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    rows = (
        await session.execute(
            select(Strategy, Security)
            .join(Security, Security.id == Strategy.security_id)
            .order_by(Strategy.generated_at.desc())
            .limit(200)
        )
    ).all()
    strategy_ids = [strategy.id for strategy, _ in rows]
    ratings = await ratings_map(session, strategy_ids, user.id)
    items = [
        {
            "id": strategy.id,
            "ticker": security.ticker,
            "name": security.name,
            "verdict": strategy.verdict,
            "horizon": strategy.horizon,
            "confidence": strategy.confidence,
            "generated_at": strategy.generated_at,
            "model_version": strategy.model_version,
            "my_rating": ratings.get(strategy.id),
        }
        for strategy, security in rows
    ]
    stats = await user_stats(session, user.id)
    context = await _base_context(session, user)
    context.update({"items": items, "stats": stats})
    return templates.TemplateResponse(request, "history.html", context)


@router.post("/api-strategy-feedback")
async def strategy_feedback_form(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    form = await request.form()
    try:
        strategy_id = int(str(form.get("strategy_id") or "0"))
    except ValueError:
        strategy_id = 0
    rating = str(form.get("rating") or "").strip()
    if strategy_id and rating:
        try:
            await set_feedback(session, strategy_id, user.id, rating)
        except (KeyError, ValueError):
            pass
    redirect = str(form.get("next") or "/history")
    if not redirect.startswith("/"):
        redirect = "/history"
    return RedirectResponse(url=redirect, status_code=303)


def _is_admin_user(user: User | None) -> bool:
    return user is not None and user.role == "admin"


@router.get("/admin")
async def admin_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if not _is_admin_user(user):
        return RedirectResponse(url="/login", status_code=303)
    runs = (
        await session.scalars(select(ScriptRun).order_by(ScriptRun.id.desc()).limit(20))
    ).all()
    usernames = {
        u.id: u.username for u in (await session.scalars(select(User))).all()
    }
    items = [
        {
            "id": run.id,
            "script": run.script_name,
            "title": (get_script(run.script_name) or {}).get("title", run.script_name),
            "status": run.status,
            "exit_code": run.exit_code,
            "started_at": run.started_at,
            "user": usernames.get(run.user_id, ""),
        }
        for run in runs
    ]
    context = await _base_context(session, user)
    context.update(
        {
            "scripts": SCRIPTS,
            "runs": items,
            "busy": is_busy(),
            "error": request.query_params.get("error") == "1",
            "param_error": request.query_params.get("error") == "2",
            "busy_error": request.query_params.get("busy") == "1",
        }
    )
    return templates.TemplateResponse(request, "admin.html", context)


@router.post("/admin/scripts/run")
async def admin_run_script(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if not _is_admin_user(user):
        return RedirectResponse(url="/login", status_code=303)
    form = await request.form()
    script_key = str(form.get("script") or "")
    script = get_script(script_key)
    if script is None:
        return RedirectResponse(url="/admin?error=1", status_code=303)
    param_value = None
    param_raw = str(form.get("param") or "").strip()
    if param_raw:
        try:
            param_value = int(param_raw)
        except ValueError:
            return RedirectResponse(url="/admin?error=2", status_code=303)
    run = ScriptRun(
        script_name=script_key,
        params={"param": param_value} if param_value is not None else {},
        user_id=user.id,
    )
    session.add(run)
    await session.commit()
    try:
        launch(run.id, script_key, param_value)
    except RuntimeError:
        return RedirectResponse(url="/admin?busy=1", status_code=303)
    except ValueError:
        return RedirectResponse(url="/admin?error=2", status_code=303)
    return RedirectResponse(url=f"/admin/runs/{run.id}", status_code=303)


@router.get("/admin/runs/{run_id}")
async def admin_run_detail(
    request: Request,
    run_id: int,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if not _is_admin_user(user):
        return RedirectResponse(url="/login", status_code=303)
    run = await session.get(ScriptRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Запуск не найден")
    context = await _base_context(session, user)
    context.update(
        {
            "run": run,
            "script_title": (get_script(run.script_name) or {}).get("title", run.script_name),
            "running": run.status == "running",
        }
    )
    return templates.TemplateResponse(request, "admin_run.html", context)


@router.get("/paper")
async def paper_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    account = await get_or_create_account(session, user.id)
    view = await account_view(session, account)
    context = await _base_context(session, user)
    context.update({"view": view})
    return templates.TemplateResponse(request, "paper.html", context)


@router.post("/api-paper-close")
async def paper_close_form(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    form = await request.form()
    ticker = str(form.get("ticker") or "").strip().upper()
    if ticker:
        account = await get_or_create_account(session, user.id)
        security = await session.scalar(select(Security).where(Security.ticker == ticker))
        if security is not None:
            closes = await latest_closes(session, [security.id])
            price = closes.get(security.id)
            if price is not None:
                await close_position(session, account, security.id, price[1])
    return RedirectResponse(url="/paper", status_code=303)


@router.post("/api-paper-reset")
async def paper_reset_form(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    account = await get_or_create_account(session, user.id)
    await reset_account(session, account)
    return RedirectResponse(url="/paper", status_code=303)


@router.get("/api/notices")
async def notices_api(
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await notice_state(session)


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
    context = await _base_context(session, user)
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
    rating = str(form.get("rating") or "").strip()
    if ticker:
        security = await session.scalar(select(Security).where(Security.ticker == ticker))
        if security is not None:
            await session.execute(
                delete(PortfolioPosition).where(
                    PortfolioPosition.user_id == user.id,
                    PortfolioPosition.security_id == security.id,
                )
            )
            recorded = False
            if rating:
                try:
                    recorded = await record_feedback_for_security(
                        session, security.id, user.id, rating
                    )
                except ValueError:
                    recorded = False
            if not recorded:
                await session.commit()
    return RedirectResponse(url="/portfolio", status_code=303)
