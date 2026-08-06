from pathlib import Path
from datetime import date, timedelta
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
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
from app.api.routes.indicators import _calculate_oi
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
    MarketOpenPosition,
    PortfolioPosition,
    ScriptRun,
    Security,
    Source,
    Strategy,
    User,
    UserSource,
    WatchlistItem,
)
from app.news.sources_service import (
    SOURCE_CATEGORIES,
    add_default_sources_for_user,
    restore_default_sources,
    user_sources,
)
from app.api.routes.sources import (
    add_source as add_source_api,
    check_sources as check_sources_api,
    remove_source as remove_source_api,
    restore_defaults as restore_defaults_api,
    search_sources as search_sources_api,
)
from app.news.feed_check import validate_feed_url
from app.schemas import FeedCheckIn, FeedSearchIn, SourceIn
from app.market.moex import MOEXClient
from app.market.oi_data import futures_for_security, nearest_future
from app.market.indicators.oi import calculate_oi
from app.market.indicators.registry import REGISTRY
from app.market.indicators.volume_profile import calculate_volume_profile
from app.news.service import load_security_news
from app.presentation.factories import WebContextFactory
from app.presentation.view import build_strategy_view
from app.macro.service import event_tickers, list_events, list_security_events
from app.strategy.engine import generate_strategy

router = APIRouter(tags=["web"])
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

RANGE_OPTIONS = {"1d": 1, "7d": 7, "1y": 365, "5y": 5 * 365, "all": None}
MAX_CHART_POINTS = 360
SIGNAL_LABELS = {
    "strong_bull": "Strong Bull",
    "strong_bear": "Strong Bear",
    "long_liquidation": "Long Liquidation",
    "short_covering": "Short Covering",
    "bearish_setup": "Bearish Setup",
    "bullish_setup": "Bullish Setup",
}

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
    if not valid:
        return None
    if len(valid) == 1:
        sampled = [valid[0], valid[0]]
    else:
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


def _chart_scale(
    values: list[float | None], width: int, height: int
) -> tuple[dict | None, list[str]]:
    """Масштаб и сегменты полилинии; None в ряду разрывает линию."""
    valid = [v for v in values if v is not None]
    if not valid:
        return None, []
    low = min(valid)
    high = max(valid)
    span = high - low or 1.0
    pad = span * 0.05
    low -= pad
    high += pad
    segments: list[str] = []
    points: list[str] = []
    n = len(values)
    for i, v in enumerate(values):
        x = round(10 + i * (width - 20) / (n - 1 if n > 1 else 1), 1)
        if v is None:
            if points:
                segments.append(" ".join(points))
                points = []
            continue
        y = round(10 + (high - v) / (high - low) * (height - 20), 1)
        points.append(f"{x},{y}")
    if points:
        segments.append(" ".join(points))
    return {"low": low, "high": high}, segments


def _build_dual_chart(
    series: list[tuple[date, float | None, float | None]],
    width: int = 900,
    height: int = 280,
) -> dict | None:
    """Две линии на одной шкале времени: (дата, oi, close) — OI и цена."""
    if not series:
        return None
    step = max(1, len(series) // MAX_CHART_POINTS)
    sampled = series[::step]
    oi_scale, oi_segments = _chart_scale(
        [s[1] for s in sampled], width, height
    )
    close_scale, close_segments = _chart_scale(
        [s[2] for s in sampled], width, height
    )
    if oi_scale is None:
        return None
    return {
        "oi_segments": oi_segments,
        "close_segments": close_segments,
        "min_oi": round(oi_scale["low"], 2),
        "max_oi": round(oi_scale["high"], 2),
        "min_close": round(close_scale["low"], 2) if close_scale else None,
        "max_close": round(close_scale["high"], 2) if close_scale else None,
        "first_date": sampled[0][0].isoformat(),
        "last_date": sampled[-1][0].isoformat(),
        "width": width,
        "height": height,
    }


def _build_volume_profile_view(meta: dict | None) -> dict | None:
    """Вид профиля объёма для SVG: узлы, POC/VAH/VAL, размеры."""
    if not meta or not meta.get("nodes"):
        return None
    nodes = meta["nodes"]
    max_volume = max((n["volume"] for n in nodes), default=0.0) or 1.0
    bar_h = 8
    step_price = (nodes[-1]["price"] - nodes[0]["price"]) / max(1, len(nodes))

    def _y(price: float) -> float:
        frac = (price - nodes[0]["price"]) / max(step_price, 1e-9) - 0.5
        return round(max(0.0, min(len(nodes) - 1, frac)) * bar_h, 1)

    return {
        "nodes": nodes,
        "poc": meta["poc"],
        "vah": meta["vah"],
        "val": meta["val"],
        "y_vah": _y(meta["vah"]),
        "y_val": _y(meta["val"]),
        "max_volume": max_volume,
        "bins": len(nodes),
        "bar_h": bar_h,
        "height": 40 + len(nodes) * bar_h,
        "min_price": nodes[0]["price"],
        "max_price": nodes[-1]["price"],
        "from": meta.get("from"),
        "to": meta.get("to"),
        "candles": meta.get("candles"),
    }


def _build_change_bars(
    pairs: list[tuple[date, float | None]],
    width: int = 900,
    height: int = 160,
) -> dict | None:
    """Столбцы изменения (например OI, %) вокруг нулевой оси."""
    if not pairs:
        return None
    step = max(1, len(pairs) // MAX_CHART_POINTS)
    sampled = pairs[::step]
    valid = [v for _, v in sampled if v is not None]
    if not valid:
        return None
    max_abs = max(abs(v) for v in valid) or 1.0
    mid = 10 + (height - 20) / 2
    bar_width = max(2.0, (width - 20) / len(sampled) * 0.7)
    rects: list[str] = []
    m = len(sampled)
    for i, (_, v) in enumerate(sampled):
        if v is None:
            continue
        x = round(10 + i * (width - 20) / (m - 1 if m > 1 else 1) - bar_width / 2, 1)
        h = round((height - 20) * v / (2 * max_abs), 1)
        y = round(mid - h, 1)
        color = "#2f7d32" if v >= 0 else "#c0392b"
        rects.append(
            f'<rect x="{x}" y="{y}" width="{round(bar_width, 1)}" '
            f'height="{abs(h)}" fill="{color}" opacity="0.85"/>'
        )
    return {
        "rects": "".join(rects),
        "zero_y": round(mid, 1),
        "max_abs": round(max_abs, 2),
        "width": width,
        "height": height,
        "first_date": sampled[0][0].isoformat(),
        "last_date": sampled[-1][0].isoformat(),
    }


def _build_volume_bars(
    pairs: list[tuple[date, float | None]],
    width: int = 900,
    height: int = 140,
) -> dict | None:
    """Столбцы объёма торгов (от нижней линии, в абсолютных значениях)."""
    if not pairs:
        return None
    step = max(1, len(pairs) // MAX_CHART_POINTS)
    sampled = pairs[::step]
    valid = [v for _, v in sampled if v is not None]
    if not valid:
        return None
    max_v = max(valid) or 1.0
    bar_width = max(2.0, (width - 20) / len(sampled) * 0.7)
    rects: list[str] = []
    m = len(sampled)
    base = height - 10
    for i, (_, v) in enumerate(sampled):
        if v is None:
            continue
        x = round(10 + i * (width - 20) / (m - 1 if m > 1 else 1) - bar_width / 2, 1)
        h = round((height - 20) * v / max_v, 1)
        y = round(base - h, 1)
        rects.append(
            f'<rect x="{x}" y="{y}" width="{round(bar_width, 1)}" '
            f'height="{h}" fill="var(--accent)" opacity="0.55"/>'
        )
    return {
        "rects": "".join(rects),
        "width": width,
        "height": height,
        "max_volume": round(max_v),
        "first_date": sampled[0][0].isoformat(),
        "last_date": sampled[-1][0].isoformat(),
    }


async def _build_oi_charts(
    session: AsyncSession,
    security: Security,
) -> tuple[dict | None, dict | None]:
    """Двойной график OI+цена и столбцы ΔOI по данным фьючерса (как на /indicators)."""
    oi_rows = (
        await session.scalars(
            select(MarketOpenPosition)
            .where(MarketOpenPosition.security_id == security.id)
            .order_by(MarketOpenPosition.trading_date)
        )
    ).all()
    if not oi_rows:
        return None, None
    candles = (
        await session.scalars(
            select(MarketCandle)
            .where(MarketCandle.security_id == security.id)
            .order_by(MarketCandle.trading_date)
        )
    ).all()
    close_by_date = {c.trading_date: c.close for c in candles}
    volume_by_date = {c.trading_date: c.volume for c in candles}
    oi_by_date = {r.trading_date: r.open_position for r in oi_rows}
    dates = sorted(set(close_by_date) | set(oi_by_date))
    result = calculate_oi(
        [
            (d, close_by_date.get(d), oi_by_date.get(d), volume_by_date.get(d))
            for d in dates
        ]
    )
    oi_vals = {v.date: v.value for v in result.values if v.kind == "oi"}
    change_vals = {v.date: v.value for v in result.values if v.kind == "oi_change_pct"}
    chart_oi = _build_dual_chart(
        [(d, oi_vals.get(d), close_by_date.get(d)) for d in dates]
    )
    chart_change = _build_change_bars(
        [(d, change_vals.get(d)) for d in dates]
    )
    return chart_oi, chart_change


def _effective_sectors(securities: list[Security]) -> dict[int, str]:
    """Эффективный сектор: у фьючерсов без сектора берётся сектор базовой акции (по assetcode)."""
    by_ticker = {s.ticker: s for s in securities}
    result = {}
    for s in securities:
        if s.sector:
            result[s.id] = s.sector
        elif s.security_type == "futures" and s.assetcode:
            base = by_ticker.get(s.assetcode)
            result[s.id] = base.sector if base else ""
        else:
            result[s.id] = s.sector
    return result


def _filter_securities(
    securities: list[Security],
    effective_sector: dict[int, str],
    sector: str,
    market: str,
    type_: str,
) -> list[Security]:
    out = []
    for s in securities:
        if type_ == "stocks" and s.security_type == "futures":
            continue
        if type_ == "futures" and s.security_type != "futures":
            continue
        if sector and effective_sector.get(s.id, "") != sector:
            continue
        if market and s.market != market:
            continue
        out.append(s)
    return out


@router.get("/")
async def index(
    request: Request,
    sector: str = "",
    market: str = "",
    type: str = "all",
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    securities = (await session.scalars(select(Security).order_by(Security.ticker))).all()

    type_ = type if type in ("stocks", "futures", "all") else "all"
    effective = _effective_sectors(securities)
    filtered = _filter_securities(securities, effective, sector, market, type_)

    sectors = sorted({eff for eff in effective.values() if eff})
    markets = sorted({s.market for s in securities if s.market})

    context = await _base_context(session, user)
    context.update(
        {
            "securities": [
                {
                    "ticker": s.ticker,
                    "name": s.name,
                    "sector": effective.get(s.id, ""),
                    "market": s.market,
                    "is_futures": s.security_type == "futures",
                }
                for s in filtered
            ],
            "sectors": sectors,
            "markets": markets,
            "current_sector": sector,
            "current_market": market,
            "current_type": type_,
        }
    )
    return templates.TemplateResponse(request, "index.html", context)


@router.get("/indicators")
async def indicators_page(
    request: Request,
    name: str = "oi",
    ticker: str = "",
    from_: date | None = Query(None, alias="from"),
    to: date | None = Query(None, alias="to"),
    oi_change_threshold_pct: float | None = Query(None, gt=0),
    vp_period: int | None = Query(None, ge=5, le=3650),
    session: AsyncSession = Depends(get_session),
):
    """Страница индикаторов: вкладки из реестра (OI, Volume Profile, ...)."""
    user = await _optional_user(request, session)
    context = await _base_context(session, user)
    indicator_name = name if name in REGISTRY else "oi"
    indicators_list = sorted(REGISTRY.items())

    if indicator_name == "volume_profile":
        vp_period_used = vp_period if vp_period is not None else 60
        vp = None
        vp_signals: list[dict] = []
        vp_error = ""
        vp_security_name = ""
        if ticker:
            security = await session.scalar(
                select(Security).where(Security.ticker == ticker.upper())
            )
            if security is None:
                vp_error = "Бумага не найдена."
            else:
                vp_security_name = security.name
                vp_candles = (
                    await session.scalars(
                        select(MarketCandle)
                        .where(
                            MarketCandle.security_id == security.id,
                            MarketCandle.trading_date
                            >= date.today()
                            - timedelta(days=max(vp_period_used * 2, 90)),
                        )
                        .order_by(MarketCandle.trading_date)
                    )
                ).all()
                vp_result = calculate_volume_profile(
                    vp_candles, params={"period": vp_period_used}
                )
                vp = _build_volume_profile_view(vp_result.meta)
                vp_signals = [
                    {"kind": s.kind, "severity": s.severity, "note": s.note}
                    for s in vp_result.signals
                ]
        context.update(
            {
                "indicator_name": indicator_name,
                "indicators_list": indicators_list,
                "ticker": ticker,
                "vp": vp,
                "vp_period": vp_period_used,
                "vp_options": [30, 60, 90, 180, 365],
                "vp_signals": vp_signals,
                "vp_error": vp_error,
                "vp_security_name": vp_security_name,
                "from": "", "to": "", "oi_threshold": 1.0, "error": "",
                "chart_oi": None, "chart_change": None, "chart_volume": None,
                "signals": [], "params_used": None, "security_name": "",
            }
        )
        return templates.TemplateResponse(request, "indicators.html", context)

    error = ""
    chart_oi = None
    chart_change = None
    chart_volume = None
    signals: list[dict] = []
    params_used = None
    security_name = ""

    if ticker:
        security = await session.scalar(
            select(Security).where(Security.ticker == ticker.upper())
        )
        if security is None:
            error = (
                "Бумага не найдена. Для фьючерсов сначала скачайте OI: "
                "админка → «Обновить открытые позиции (OI)» или "
                "scripts.update_oi --ticker <SECID>."
            )
        else:
            security_name = security.name
            try:
                result = await _calculate_oi(
                    session,
                    security,
                    from_date=from_,
                    till_date=to,
                    limit=None,
                    params={
                        "oi_change_threshold_pct": oi_change_threshold_pct,
                        "price_change_threshold_pct": None,
                    },
                )
            except HTTPException as exc:
                error = str(exc.detail)
                result = None
            if result is not None:
                params_used = result.params
                candle_q = select(MarketCandle).where(
                    MarketCandle.security_id == security.id
                )
                if from_ is not None:
                    candle_q = candle_q.where(MarketCandle.trading_date >= from_)
                if to is not None:
                    candle_q = candle_q.where(MarketCandle.trading_date <= to)
                candles = (
                    await session.scalars(
                        candle_q.order_by(MarketCandle.trading_date)
                    )
                ).all()
                close_by_date = {c.trading_date: c.close for c in candles}
                volume_by_date = {c.trading_date: c.volume for c in candles}
                oi_by_date = {
                    v.date: v.value for v in result.values if v.kind == "oi"
                }
                change_by_date = {
                    v.date: v.value
                    for v in result.values
                    if v.kind == "oi_change_pct"
                }
                volume_change_by_date = {
                    v.date: v.value
                    for v in result.values
                    if v.kind == "volume_change_pct"
                }
                dates = sorted(set(close_by_date) | set(oi_by_date))
                chart_oi = _build_dual_chart(
                    [(d, oi_by_date.get(d), close_by_date.get(d)) for d in dates]
                )
                chart_change = _build_change_bars(
                    [(d, change_by_date.get(d)) for d in dates]
                )
                chart_volume = _build_volume_bars(
                    [(d, volume_by_date.get(d)) for d in dates]
                )
                ordered_signals = sorted(
                    result.signals, key=lambda s: s.date, reverse=True
                )
                signals = [
                    {
                        "date": s.date.strftime("%d.%m.%Y"),
                        "kind": s.kind,
                        "label": SIGNAL_LABELS.get(s.kind, s.kind),
                        "severity": s.severity,
                        "note": s.note,
                        "volume": s.volume,
                        "volume_value": volume_by_date.get(s.date),
                        "volume_change_pct": volume_change_by_date.get(s.date),
                    }
                    for s in ordered_signals
                ]

    context.update(
        {
            "ticker": ticker,
            "from": from_.isoformat() if from_ else "",
            "to": to.isoformat() if to else "",
            "oi_threshold": (
                oi_change_threshold_pct if oi_change_threshold_pct is not None else 1.0
            ),
            "error": error,
            "chart_oi": chart_oi,
            "chart_change": chart_change,
            "chart_volume": chart_volume,
            "signals": signals,
            "params_used": params_used,
            "security_name": security_name,
            "indicator_name": indicator_name,
            "indicators_list": indicators_list,
            "vp": None,
            "vp_period": 60,
            "vp_options": [30, 60, 90, 180, 365],
            "vp_signals": [],
            "vp_error": "",
            "vp_security_name": "",
        }
    )
    return templates.TemplateResponse(request, "indicators.html", context)


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
    vp_period: int | None = Query(None, ge=5, le=3650),
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

    vp_days = vp_period if vp_period is not None else 60
    vp_candles = (
        await session.scalars(
            select(MarketCandle)
            .where(
                MarketCandle.security_id == security.id,
                MarketCandle.trading_date
                >= date.today() - timedelta(days=max(vp_days * 2, 90)),
            )
            .order_by(MarketCandle.trading_date)
        )
    ).all()
    vp = _build_volume_profile_view(
        calculate_volume_profile(vp_candles, params={"period": vp_days}).meta
    )

    futures = await futures_for_security(session, security.ticker)
    futures_items = [
        {
            "ticker": f.ticker,
            "name": f.name,
            "lastdeldate": f.lastdeldate.isoformat() if f.lastdeldate else "",
        }
        for f in futures
    ]
    nearest = await nearest_future(session, security.ticker)
    chart_oi = None
    chart_change = None
    nearest_oi = None
    if nearest is not None:
        chart_oi, chart_change = await _build_oi_charts(session, nearest)
        if chart_oi:
            nearest_oi = {
                "ticker": nearest.ticker,
                "name": nearest.name,
                "lastdeldate": nearest.lastdeldate.isoformat() if nearest.lastdeldate else "",
            }

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
            "futures": futures_items,
            "nearest_oi": nearest_oi,
            "chart_oi": chart_oi,
            "chart_change": chart_change,
            "vp": vp,
            "vp_period": vp_days,
            "vp_options": [30, 60, 90, 180, 365],
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
    await add_default_sources_for_user(session, user.id)
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
    # Чекбокс «скачать OI по всем фьючерсам» на карточке update_oi
    if form.get("param_all") is not None and script_key == "update_oi":
        script_key = "update_oi_all"
    script = get_script(script_key)
    if script is None:
        return RedirectResponse(url="/admin?error=1", status_code=303)

    param_values: dict | None = None
    if script_key == "update_oi_all":
        run_params = {"all": True}
    elif script.get("params"):
        # Несколько параметров: поля формы param_<flag без "--">
        param_values = {}
        for p in script["params"]:
            flag, label, default, *rest = p
            ptype = rest[0] if rest else "int"
            raw = str(form.get(f"param_{flag[2:]}") or "").strip()
            if ptype == "text":
                param_values[flag] = raw if raw else str(default)
            else:
                try:
                    param_values[flag] = int(raw) if raw else int(default)
                except ValueError:
                    return RedirectResponse(url="/admin?error=2", status_code=303)
        run_params = {"params": param_values}
    else:
        param_raw = str(form.get("param") or "").strip()
        if param_raw:
            param_type = (
                script["param"][3]
                if script["param"] is not None and len(script["param"]) > 3
                else "int"
            )
            if param_type == "text":
                param_values = {script["param"][0]: param_raw}
            else:
                try:
                    param_values = {script["param"][0]: int(param_raw)}
                except ValueError:
                    return RedirectResponse(url="/admin?error=2", status_code=303)
        run_params = {"params": param_values} if param_values else {}

    run = ScriptRun(
        script_name=script_key,
        params=run_params,
        user_id=user.id,
    )
    session.add(run)
    await session.commit()
    try:
        launch(run.id, script_key, param_values)
    except RuntimeError:
        return RedirectResponse(url="/admin?busy=1", status_code=303)
    except ValueError:
        return RedirectResponse(url="/admin?error=2", status_code=303)
    return RedirectResponse(url=f"/admin/runs/{run.id}", status_code=303)


def _run_progress(output: str | None) -> dict | None:
    """Прогресс «[i/N]» из лога скрипта (например, фьючерсов загружено из общего числа)."""
    if not output:
        return None
    matches = list(re.finditer(r"\[(\d+)/(\d+)\]", output))
    if not matches:
        return None
    last = matches[-1]
    done = int(last.group(1))
    total = int(last.group(2))
    if total <= 0:
        return None
    return {
        "done": done,
        "total": total,
        "remaining": total - done,
        "pct": round(done / total * 100),
    }


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
            "progress": _run_progress(run.output),
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


@router.get("/news")
async def news_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    feeds = await user_sources(session, user.id, kind="rss")
    context = await _base_context(session, user)
    context.update(
        {
            "feeds": feeds,
            "categories": SOURCE_CATEGORIES,
            "error": request.query_params.get("error", ""),
            "info": request.query_params.get("info", ""),
            "candidates": [],
        }
    )
    return templates.TemplateResponse(request, "news.html", context)


@router.post("/news/rss/add")
async def news_rss_add(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    form = await request.form()
    name = str(form.get("name") or "").strip()
    url = str(form.get("url") or "").strip()
    category = str(form.get("category") or "")
    if not name or not url:
        return RedirectResponse(url="/news?error=Заполните имя и URL", status_code=303)
    if category not in SOURCE_CATEGORIES and category != "":
        return RedirectResponse(url="/news?error=Недопустимая категория", status_code=303)
    err = validate_feed_url(url)
    if err:
        return RedirectResponse(url=f"/news?error={err}", status_code=303)
    try:
        await add_source_api(SourceIn(name=name, url=url, category=category), user, session)
    except HTTPException as exc:
        return RedirectResponse(url=f"/news?error={exc.detail}", status_code=303)
    return RedirectResponse(url="/news", status_code=303)


@router.post("/news/rss/remove")
async def news_rss_remove(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    form = await request.form()
    try:
        source_id = int(str(form.get("source_id") or "0"))
    except ValueError:
        source_id = 0
    try:
        await remove_source_api(source_id, user, session)
    except HTTPException:
        pass
    return RedirectResponse(url="/news", status_code=303)


@router.post("/news/rss/check")
async def news_rss_check(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    form = await request.form()
    ids = []
    for value in form.getlist("ids"):
        try:
            ids.append(int(str(value)))
        except ValueError:
            continue
    try:
        await check_sources_api(FeedCheckIn(ids=ids), user, session)
    except HTTPException:
        return RedirectResponse(url="/news?error=Не удалось проверить ленты", status_code=303)
    return RedirectResponse(
        url=f"/news?info=Проверено лент: {len(ids) if ids else 'все'}", status_code=303
    )


@router.post("/news/rss/restore")
async def news_rss_restore(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    added = await restore_defaults_api(user, session)
    return RedirectResponse(
        url=f"/news?info=Добавлено стандартных лент: {added}", status_code=303
    )


@router.post("/news/rss/search")
async def news_rss_search(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    form = await request.form()
    query = str(form.get("query") or "").strip()
    if not query:
        return RedirectResponse(url="/news?error=Введите тему для поиска", status_code=303)
    try:
        candidates = await search_sources_api(FeedSearchIn(query=query), user, session)
    except HTTPException as exc:
        return RedirectResponse(url=f"/news?error={exc.detail}", status_code=303)
    feeds = await user_sources(session, user.id, kind="rss")
    context = await _base_context(session, user)
    context.update(
        {
            "feeds": feeds,
            "categories": SOURCE_CATEGORIES,
            "error": "",
            "info": "",
            "candidates": candidates,
            "query": query,
        }
    )
    return templates.TemplateResponse(request, "news.html", context)


@router.post("/news/rss/add-selected")
async def news_rss_add_selected(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    form = await request.form()
    names = form.getlist("cand_name")
    urls = form.getlist("cand_url")
    cats = form.getlist("cand_category")
    added = 0
    for name, url, category in zip(names, urls, cats):
        name = str(name).strip()
        url = str(url).strip()
        category = str(category or "")
        if not name or not url:
            continue
        if validate_feed_url(url):
            continue
        try:
            await add_source_api(
                SourceIn(name=name, url=url, category=category), user, session
            )
            added += 1
        except HTTPException:
            continue
    return RedirectResponse(
        url=f"/news?info=Добавлено лент: {added}", status_code=303
    )
