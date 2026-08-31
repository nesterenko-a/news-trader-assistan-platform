from pathlib import Path
from datetime import date, datetime, timedelta, timezone
import os
import re
import uuid
import json

import httpx

from app.config import get_settings as get_cfg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
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
from app.admin.runner import SCRIPTS, _pipeline_failed_phase, get_script, is_busy, is_daemon_busy, launch
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
from app.notices.service import dismiss_all_notices, dismiss_notice, notice_state
from app.db.connection import get_session
from app.db.models import (
    FuturesTemplate,
    Influence,
    Entity,
    MarketCandle,
    MarketOpenPosition,
    PortfolioPosition,
    RealtimeConfig,
    ScriptRun,
    Security,
    Source,
    Strategy,
    TechAnalysis,
    User,
    UserPipelinePref,
    UserSource,
    WatchlistItem,
)
from app.news.sources_service import (
    SOURCE_CATEGORIES,
    add_default_sources_for_user,
    restore_default_sites,
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
from app.news.feed_check import check_website, validate_feed_url
from app.schemas import FeedCheckIn, FeedSearchIn, SourceIn
from app.market.moex import MOEXClient
from app.market.realtime import ensure_config as realtime_ensure_config
from app.market.oi_data import (
    client_groups_series,
    futures_for_security,
    nearest_future,
)
from app.market.indicators.oi import calculate_oi
from app.market.indicators.registry import REGISTRY
from app.market.indicators.volume_profile import calculate_volume_profile
from app.market.indicators.support_resistance import calculate_support_resistance
from app.market.indicators.ema import calculate_ema
from app.market.indicators.macd import calculate_macd
from app.market.indicators.bollinger import calculate_bollinger
from app.market.indicators.atr import calculate_atr
from app.market.indicators.adx import calculate_adx
from app.market.indicators.rsi_indicator import calculate_rsi
from app.market.indicators.basis_service import basis_for_ticker
from app.tech_analysis.service import has_active, list_analyses, retry_analysis, start_analysis
from app.news.service import load_security_news
from app.presentation.factories import WebContextFactory
from app.presentation.markdown_renderer import render_markdown
from app.presentation.view import build_strategy_view
from app.macro.service import event_tickers, list_events, list_security_events
from app.strategy.engine import generate_strategy
from app.graph.service import (
    add_influence_with_source,
    export_graph_records,
    graph_to_jsonl,
    resolve_entity_id,
)
from app.graph.map import build_dependency_map
from app.graph.map_view import build_map_svg

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
    "cross_up": "Golden Cross",
    "cross_down": "Death Cross",
    "hist_positive": "Гистограмма положительная",
    "hist_negative": "Гистограмма отрицательная",
    "touch_upper": "Касание верхней полосы",
    "touch_lower": "Касание нижней полосы",
    "revert_in": "Возврат внутрь полос",
    "squeeze": "Сжатие полос (squeeze)",
    "bullish": "Бычий настрой",
    "bearish": "Медвежий настрой",
    "trend": "Тренд",
    "range": "Флэт (диапазон)",
}

_web_context_factory = WebContextFactory()
_moex = MOEXClient()


def _build_indicator_charts(
    series_by_kind: dict[str, list[tuple]], width: int = 900, height: int = 180
) -> list[dict]:
    """Линейные графики по сериям значений индикатора (kind -> [(date, value)])."""
    charts: list[dict] = []
    for kind, points in series_by_kind.items():
        valid = [(d, v) for d, v in points if v is not None]
        if len(valid) < 2:
            continue
        vals = [v for _, v in valid]
        vmin, vmax = min(vals), max(vals)
        span = (vmax - vmin) or 1.0
        n = len(valid)
        coords = []
        for i, (_, v) in enumerate(valid):
            x = 10 + i * (width - 20) / (n - 1)
            y = height - 10 - (v - vmin) / span * (height - 20)
            coords.append(f"{x:.1f},{y:.1f}")
        charts.append(
            {
                "kind": kind,
                "points": " ".join(coords),
                "min": round(vmin, 4),
                "max": round(vmax, 4),
                "first_date": valid[0][0].isoformat(),
                "last_date": valid[-1][0].isoformat(),
                "width": width,
                "height": height,
                "count": n,
            }
        )
    return charts


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


def _axis_ticks(low: float, high: float, n: int = 5) -> list[float]:
    """n равномерных значений между low и high для шкалы оси."""
    return [low + (high - low) * i / (n - 1) for i in range(n)]


def _build_dual_chart(
    series: list[tuple[date, float | None, float | None]],
    width: int = 900,
    height: int = 280,
) -> dict | None:
    """Две линии на одной шкале времени: (дата, oi, close) — OI и цена.

    Возвращает также метки осей: даты по X, значения OI и цены по Y.
    """
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

    n = len(sampled)

    def _x(i: int) -> float:
        return round(10 + i * (width - 20) / (n - 1 if n > 1 else 1), 1)

    def _y(scale: dict, v: float) -> float:
        return round(
            10 + (scale["high"] - v) / (scale["high"] - scale["low"])
            * (height - 20),
            1,
        )

    idxs = sorted({round(i * (n - 1) / 4) for i in range(5)}) if n > 1 else [0]
    date_ticks = [
        (_x(i), sampled[i][0].strftime("%d.%m")) for i in idxs
    ]
    oi_ticks = [
        (8, _y(oi_scale, v), f"{v:.2f}") for v in _axis_ticks(oi_scale["low"], oi_scale["high"])
    ]
    close_ticks = []
    if close_scale:
        close_ticks = [
            (width - 8, _y(close_scale, v), f"{v:.2f}")
            for v in _axis_ticks(close_scale["low"], close_scale["high"])
        ]
    return {
        "oi_segments": oi_segments,
        "close_segments": close_segments,
        "min_oi": round(oi_scale["low"], 2),
        "max_oi": round(oi_scale["high"], 2),
        "min_close": round(close_scale["low"], 2) if close_scale else None,
        "max_close": round(close_scale["high"], 2) if close_scale else None,
        "first_date": sampled[0][0].isoformat(),
        "last_date": sampled[-1][0].isoformat(),
        "date_ticks": date_ticks,
        "oi_ticks": oi_ticks,
        "close_ticks": close_ticks,
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


def _build_net_bars(
    series: list[tuple[date, float | None, float | None]],
    close_series: list[tuple[date, float | None]],
    width: int = 900,
    height: int = 320,
) -> dict | None:
    """COT-график нетто-позиций групп (long − short) под ценой.

    Верхняя панель — линия цены close; нижняя — группированные столбцы
    нетто-позиций физиков (net_ph) и юриков (net_jur) вокруг нулевой оси
    (пересечение нуля = смена перевеса группы). Канон COT-визуализации.
    """
    if not series:
        return None
    step = max(1, len(series) // MAX_CHART_POINTS)
    sampled = series[::step]
    n = len(sampled)
    top_h = 110
    bottom_top = top_h + 26
    bottom_h = height - bottom_top - 14

    def _x(i: int) -> float:
        return round(10 + i * (width - 20) / (n - 1 if n > 1 else 1), 1)

    # --- верхняя панель: цена ---
    close_segments: list[str] = []
    close_ticks: list[tuple[float, float, str]] = []
    close_vals = [c for _, c in close_series if c is not None]
    if close_vals:
        low, high = min(close_vals), max(close_vals)
        span = high - low or 1.0
        pad = span * 0.08
        low -= pad
        high += pad

        def _cy(v: float) -> float:
            return round(8 + (high - v) / (high - low) * (top_h - 16), 1)

        points: list[str] = []
        for i, (_, c) in enumerate(close_series[:n] or close_series):
            if i >= n:
                break
            if c is None:
                if points:
                    close_segments.append(" ".join(points))
                    points = []
                continue
            points.append(f"{_x(i)},{_cy(c)}")
        if points:
            close_segments.append(" ".join(points))
        close_ticks = [
            (width - 8, _cy(v), f"{v:.2f}")
            for v in (low, (low + high) / 2, high)
        ]

    # --- нижняя панель: нетто-позиции ---
    net_vals = [a for _, a, b in sampled if a is not None] + [
        b for _, a, b in sampled if b is not None
    ]
    max_abs = max((abs(v) for v in net_vals), default=0.0) or 1.0
    scale = max_abs * 1.12
    mid = bottom_top + bottom_h / 2

    def _ny(v: float) -> float:
        return round(mid - v / scale * (bottom_h / 2 - 6), 1)

    zero_y = _ny(0)
    bar_width = max(2.0, (width - 20) / n * 0.32)
    rects: list[dict] = []
    for i, (_, a, b) in enumerate(sampled):
        x = _x(i)
        for group, v in (("ph", a), ("jur", b)):
            if v is None:
                continue
            y = _ny(v)
            h = abs(zero_y - y)
            if h < 0.4:
                continue
            rects.append(
                {
                    "x": round(x - bar_width * 1.5 + (0 if group == "ph" else bar_width), 1),
                    "y": round(min(y, zero_y), 1),
                    "w": round(bar_width, 1),
                    "h": round(h, 1),
                    "group": group,
                }
            )

    # Сетка нижней панели: уровни −scale … +scale (нулевая линия выделена отдельно)
    net_grid = [(_ny(v), f"{v:.2f}") for v in (-scale, -scale / 2, 0, scale / 2, scale)]
    # Сетка верхней панели (цена)
    close_grid: list[tuple[float, float, str]] = []
    if close_vals:
        for v in (low, (low + high) / 2, high):
            close_grid.append((10, _cy(v), f"{v:g}"))

    idxs = sorted({round(i * (n - 1) / 4) for i in range(5)}) if n > 1 else [0]
    date_ticks = [(_x(i), sampled[i][0].strftime("%d.%m")) for i in idxs]
    net_ticks = [
        (8, _ny(v), f"{v:.2f}") for v in (-scale, 0, scale)
    ]
    return {
        "width": width,
        "height": height,
        "close_segments": close_segments,
        "close_ticks": close_ticks,
        "net_rects": rects,
        "zero_y": round(zero_y, 1),
        "net_scale": round(scale, 2),
        "net_ticks": net_ticks,
        "net_grid": net_grid,
        "close_grid": close_grid,
        "date_ticks": date_ticks,
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


def _map_to_cytoscape(graph: dict) -> dict:
    """Преобразовать граф зависимостей в структуру для Cytoscape.js (nodes/edges)."""
    nodes = []
    for n in graph.get("nodes", []):
        nodes.append(
            {
                "data": {
                    "id": n["name"],
                    "label": n["name"],
                    "type": n.get("type"),
                    "is_target": bool(n.get("is_target")),
                    "is_key": bool(n.get("is_key")),
                    "metrics": n.get("metrics") or [],
                }
            }
        )
    edges = []
    for e in graph.get("edges", []):
        mechanism = (e.get("mechanism") or "").strip() or "связь"
        if len(mechanism) > 60:
            mechanism = mechanism[:57] + "…"
        edges.append(
            {
                "data": {
                    "id": f"{e['from']}→{e['to']}",
                    "source": e["from"],
                    "target": e["to"],
                    "sign": float(e.get("sign", 1.0)),
                    "strength": e.get("strength") or "medium",
                    "label": mechanism,
                    "kind": e.get("kind"),
                }
            }
        )
    return {"nodes": nodes, "edges": edges}


@router.get("/map")
async def map_page(
    request: Request,
    ticker: str = "",
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    securities = (
        await session.scalars(
            select(Security)
            .where(Security.security_type != "futures")
            .order_by(Security.ticker)
        )
    ).all()

    selected = None
    dep_map = ""
    dep_count = 0
    graph_json = "null"
    if ticker.strip():
        selected = await session.scalar(
            select(Security).where(Security.ticker == ticker.strip().upper())
        )
        if selected is None:
            raise HTTPException(status_code=404, detail="Бумага не найдена")
        graph = await build_dependency_map(session, selected.id)
        dep_map = build_map_svg(graph)["svg"]
        dep_count = len(graph["nodes"])
        graph_json = json.dumps(_map_to_cytoscape(graph), ensure_ascii=False)

    context = await _base_context(session, user)
    context.update(
        {
            "securities": [
                {
                    "ticker": s.ticker,
                    "name": s.name,
                    "sector": s.sector,
                    "is_futures": s.security_type == "futures",
                }
                for s in securities
            ],
            "selected_ticker": selected.ticker if selected else "",
            "selected_name": selected.name if selected else "",
            "dep_map": dep_map,
            "dep_count": dep_count,
            "graph_json": graph_json,
        }
    )
    return templates.TemplateResponse(request, "map.html", context)


@router.get("/indicators")
async def indicators_page(
    request: Request,
    name: str = "oi",
    ticker: str = "",
    from_: date | None = Query(None, alias="from"),
    to: date | None = Query(None, alias="to"),
    oi_change_threshold_pct: float | None = Query(None, gt=0),
    vp_period: int | None = Query(None, ge=5, le=3650),
    fast: int | None = Query(None, ge=2, le=500),
    slow: int | None = Query(None, ge=3, le=500),
    signal: int | None = Query(None, ge=2, le=100),
    sr_window: int | None = Query(None, ge=10, le=500),
    bb_period: int | None = Query(None, ge=5, le=500),
    bb_k: float | None = Query(None, gt=0, le=5),
    atr_period: int | None = Query(None, ge=2, le=500),
    adx_period: int | None = Query(None, ge=2, le=100),
    rsi_period: int | None = Query(None, ge=2, le=100),
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

    if indicator_name in ("ema", "macd"):
        ema_error = ""
        ema_security_name = ""
        ema_charts: list[dict] = []
        ema_signals: list[dict] = []
        ema_meta: dict = {}
        ema_params = {"fast": fast, "slow": slow, "signal": signal}
        if ticker:
            security = await session.scalar(
                select(Security).where(Security.ticker == ticker.upper())
            )
            if security is None:
                ema_error = "Бумага не найдена."
            else:
                ema_security_name = security.name
                eff_fast = fast if fast is not None else 12
                eff_slow = slow if slow is not None else 26
                if eff_fast >= eff_slow:
                    ema_error = "fast должен быть меньше slow"
                else:
                    candle_q = (
                        select(MarketCandle)
                        .where(
                            MarketCandle.security_id == security.id,
                            MarketCandle.close.is_not(None),
                        )
                        .order_by(MarketCandle.trading_date)
                    )
                    if from_ is not None:
                        candle_q = candle_q.where(MarketCandle.trading_date >= from_)
                    if to is not None:
                        candle_q = candle_q.where(MarketCandle.trading_date <= to)
                    candles = (
                        await session.scalars(candle_q)
                    ).all()[-300:]
                    result = (
                        calculate_ema(candles, params=ema_params)
                        if indicator_name == "ema"
                        else calculate_macd(candles, params=ema_params)
                    )
                    ema_meta = result.meta
                    series: dict[str, list[tuple]] = {}
                    for value in result.values:
                        series.setdefault(value.kind, []).append(
                            (value.date, value.value)
                        )
                    ema_charts = _build_indicator_charts(series)
                    ema_signals = [
                        {
                            "date": s.date.strftime("%d.%m.%Y"),
                            "kind": s.kind,
                            "label": SIGNAL_LABELS.get(s.kind, s.kind),
                            "severity": s.severity,
                            "note": s.note,
                        }
                        for s in sorted(result.signals, key=lambda s: s.date, reverse=True)
                    ]
        context.update(
            {
                "indicator_name": indicator_name,
                "indicators_list": indicators_list,
                "ticker": ticker,
                "ema_error": ema_error,
                "ema_security_name": ema_security_name,
                "ema_charts": ema_charts,
                "ema_signals": ema_signals,
                "ema_meta": ema_meta,
                "ema_params": ema_params,
                "from": from_.isoformat() if from_ else "",
                "to": to.isoformat() if to else "",
                "oi_threshold": 1.0,
                "error": "",
                "chart_oi": None,
                "chart_change": None,
                "chart_volume": None,
                "signals": [],
                "params_used": None,
                "security_name": "",
            }
        )
        return templates.TemplateResponse(request, "indicators.html", context)

    if indicator_name == "support_resistance":
        sr_window_used = sr_window if sr_window is not None else 20
        sr = None
        sr_signals: list[dict] = []
        sr_error = ""
        sr_security_name = ""
        if ticker:
            security = await session.scalar(
                select(Security).where(Security.ticker == ticker.upper())
            )
            if security is None:
                sr_error = "Бумага не найдена."
            else:
                sr_security_name = security.name
                candle_q = (
                    select(MarketCandle)
                    .where(
                        MarketCandle.security_id == security.id,
                        MarketCandle.close.is_not(None),
                    )
                    .order_by(MarketCandle.trading_date)
                )
                candles = (await session.scalars(candle_q)).all()[-max(sr_window_used * 3, 90):]
                sr_result = calculate_support_resistance(
                    candles, params={"window": sr_window_used}
                )
                sr = sr_result.meta if sr_result.meta.get("levels") else None
                sr_signals = [
                    {"kind": s.kind, "severity": s.severity, "note": s.note}
                    for s in sr_result.signals
                ]
        context.update(
            {
                "indicator_name": indicator_name,
                "indicators_list": indicators_list,
                "ticker": ticker,
                "sr": sr,
                "sr_window": sr_window_used,
                "sr_options": [10, 20, 30, 50, 100],
                "sr_signals": sr_signals,
                "sr_error": sr_error,
                "sr_security_name": sr_security_name,
                "from": "", "to": "", "oi_threshold": 1.0, "error": "",
                "chart_oi": None, "chart_change": None, "chart_volume": None,
                "signals": [], "params_used": None, "security_name": "",
            }
        )
        return templates.TemplateResponse(request, "indicators.html", context)

    if indicator_name == "basis":
        basis_window = sr_window if sr_window is not None else 5
        basis_err = ""
        basis_security_name = ""
        basis_charts: list[dict] = []
        basis_signals: list[dict] = []
        basis_meta: dict = {}
        if ticker:
            basis_result = await basis_for_ticker(
                session, ticker, params={"window": basis_window}
            )
            basis_sec = await session.scalar(
                select(Security).where(Security.ticker == ticker.upper())
            )
            basis_security_name = basis_sec.name if basis_sec else ""
            basis_meta = basis_result.meta
            series: dict[str, list[tuple]] = {}
            for value in basis_result.values:
                series.setdefault(value.kind, []).append((value.date, value.value))
            basis_charts = _build_indicator_charts(series)
            basis_signals = [
                {
                    "date": s.date.strftime("%d.%m.%Y"),
                    "kind": s.kind,
                    "label": SIGNAL_LABELS.get(s.kind, s.kind),
                    "severity": s.severity,
                    "note": s.note,
                }
                for s in sorted(basis_result.signals, key=lambda s: s.date, reverse=True)
            ]
            if basis_meta.get("note"):
                basis_err = basis_meta["note"]
            elif not basis_result.values:
                basis_err = "Нет данных для расчёта базиса (нужны свечи фьючерса и спота)."
        context.update(
            {
                "indicator_name": indicator_name,
                "indicators_list": indicators_list,
                "ticker": ticker,
                "basis_error": basis_err,
                "basis_security_name": basis_security_name,
                "basis_charts": basis_charts,
                "basis_signals": basis_signals,
                "basis_meta": basis_meta,
                "basis_window": basis_window,
                "from": from_.isoformat() if from_ else "",
                "to": to.isoformat() if to else "",
                "oi_threshold": 1.0, "error": "",
                "chart_oi": None, "chart_change": None, "chart_volume": None,
                "signals": [], "params_used": None, "security_name": "",
            }
        )
        return templates.TemplateResponse(request, "indicators.html", context)

    if indicator_name in ("bollinger", "atr", "adx", "rsi"):
        bb_period_used = bb_period if bb_period is not None else 20
        bb_k_used = bb_k if bb_k is not None else 2.0
        atr_period_used = atr_period if atr_period is not None else 14
        adx_period_used = adx_period if adx_period is not None else 14
        rsi_period_used = rsi_period if rsi_period is not None else 14
        tech_error = ""
        tech_security_name = ""
        tech_charts: list[dict] = []
        tech_signals: list[dict] = []
        tech_meta: dict = {}
        if ticker:
            security = await session.scalar(
                select(Security).where(Security.ticker == ticker.upper())
            )
            if security is None:
                tech_error = "Бумага не найдена."
            else:
                tech_security_name = security.name
                candle_q = (
                    select(MarketCandle)
                    .where(
                        MarketCandle.security_id == security.id,
                        MarketCandle.close.is_not(None),
                    )
                    .order_by(MarketCandle.trading_date)
                )
                if from_ is not None:
                    candle_q = candle_q.where(MarketCandle.trading_date >= from_)
                if to is not None:
                    candle_q = candle_q.where(MarketCandle.trading_date <= to)
                candles = (await session.scalars(candle_q)).all()[-300:]
                if indicator_name == "bollinger":
                    result = calculate_bollinger(
                        candles,
                        params={"period": bb_period_used, "k": bb_k_used},
                    )
                elif indicator_name == "atr":
                    result = calculate_atr(candles, params={"period": atr_period_used})
                elif indicator_name == "rsi":
                    result = calculate_rsi(candles, params={"period": rsi_period_used})
                else:
                    result = calculate_adx(candles, params={"period": adx_period_used})
                tech_meta = result.meta
                series: dict[str, list[tuple]] = {}
                for value in result.values:
                    series.setdefault(value.kind, []).append((value.date, value.value))
                tech_charts = _build_indicator_charts(series)
                tech_signals = [
                    {
                        "date": s.date.strftime("%d.%m.%Y"),
                        "kind": s.kind,
                        "label": SIGNAL_LABELS.get(s.kind, s.kind),
                        "severity": s.severity,
                        "note": s.note,
                    }
                    for s in sorted(result.signals, key=lambda s: s.date, reverse=True)
                ]
        context.update(
            {
                "indicator_name": indicator_name,
                "indicators_list": indicators_list,
                "ticker": ticker,
                "tech_error": tech_error,
                "tech_security_name": tech_security_name,
                "tech_charts": tech_charts,
                "tech_signals": tech_signals,
                "tech_meta": tech_meta,
                "bb_period": bb_period_used,
                "bb_k": bb_k_used,
                "atr_period": atr_period_used,
                "adx_period": adx_period_used,
                "rsi_period": rsi_period_used,
                "from": from_.isoformat() if from_ else "",
                "to": to.isoformat() if to else "",
                "oi_threshold": 1.0, "error": "",
                "chart_oi": None, "chart_change": None, "chart_volume": None,
                "signals": [], "params_used": None, "security_name": "",
            }
        )
        return templates.TemplateResponse(request, "indicators.html", context)

    error = ""
    chart_oi = None
    chart_change = None
    chart_volume = None
    client_groups: list[dict] = []
    chart_groups_net = None
    totals: dict = {}
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
                client_groups = await client_groups_series(
                    session, security.id, from_, to
                )
                chart_groups_net = _build_net_bars(
                    [
                        (
                            g["date"],
                            (g.get("physical") or {}).get("net"),
                            (g.get("juridical") or {}).get("net"),
                        )
                        for g in client_groups
                    ],
                    [(g["date"], close_by_date.get(g["date"])) for g in client_groups],
                )
                totals = {}
                if client_groups:
                    def _sum(side: str, key: str) -> int:
                        return sum(
                            (g.get(side) or {}).get(key, 0) for g in client_groups
                        )

                    totals = {
                        "physical_long": _sum("physical", "long"),
                        "physical_short": _sum("physical", "short"),
                        "physical_net": _sum("physical", "net"),
                        "juridical_long": _sum("juridical", "long"),
                        "juridical_short": _sum("juridical", "short"),
                        "juridical_net": _sum("juridical", "net"),
                        "summary": sum(g.get("summary") or 0 for g in client_groups),
                    }
                    total_summary = totals["summary"]
                    totals["physical_share_pct"] = (
                        round(totals["physical_long"] * 100.0 / total_summary, 1)
                        if total_summary
                        else None
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

    oi_live = None
    if indicator_name == "oi" and ticker:
        from app.api.routes.realtime import _latest_oi
        oi_sec = await session.scalar(select(Security).where(Security.ticker == ticker.upper()))
        if oi_sec is not None:
            oi_live = await _latest_oi(session, oi_sec.id)
    context.update(
        {
            "ticker": ticker,
            "from": from_.isoformat() if from_ else "",
            "to": to.isoformat() if to else "",
            "oi_threshold": (
                oi_change_threshold_pct if oi_change_threshold_pct is not None else 1.0
            ),
            "oi_live": oi_live,
            "error": error,
            "chart_oi": chart_oi,
            "chart_change": chart_change,
            "chart_volume": chart_volume,
            "client_groups": client_groups,
            "chart_groups_net": chart_groups_net,
            "client_groups_totals": totals,
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
    ta_page: int = 1,
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
        latest_oi = None
        if nearest.id:
            from app.api.routes.realtime import _latest_oi
            latest_oi = await _latest_oi(session, nearest.id)
        if chart_oi:
            nearest_oi = {
                "ticker": nearest.ticker,
                "name": nearest.name,
                "lastdeldate": nearest.lastdeldate.isoformat() if nearest.lastdeldate else "",
                "oi_open": (latest_oi or {}).get("open_position"),
                "oi_change_pct": (latest_oi or {}).get("change_pct"),
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
    dep_map_data = await build_dependency_map(session, security.id)
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
            "dep_map": build_map_svg(dep_map_data)["svg"],
            "dep_graph": json.dumps(_map_to_cytoscape(dep_map_data), ensure_ascii=False),
        }
    )
    # «Теханализ в LLM» доступен только для акций и фьючерсов
    context["is_analyzable"] = security.security_type in ("stock", "futures")
    context["ta_active"] = has_active(security.ticker) if context["is_analyzable"] else False
    if context["is_analyzable"]:
        try:
            context["tech_analyses"] = await list_analyses(security.ticker, page=ta_page)
        except Exception as exc:  # noqa: BLE001
            print(f"[security_page] list_analyses error: {type(exc).__name__}: {exc}", flush=True)
            context["tech_analyses"] = {"items": [], "pages": 1, "page": 1}
        _cfg = get_cfg()
        context["llm_models"] = {
            "chatgpt": _cfg.chatgpt_model,
            "deepseek": _cfg.llm_model,
        }
    else:
        context["tech_analyses"] = {"items": [], "pages": 1, "page": 1}
    return templates.TemplateResponse(request, "security.html", context)


@router.post("/securities/{ticker}/tech-analysis")
async def security_tech_analysis_start(
    ticker: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    security = await session.scalar(
        select(Security).where(Security.ticker == ticker.upper())
    )
    if security is None or security.security_type not in ("stock", "futures"):
        return RedirectResponse(url=f"/securities/{ticker}", status_code=303)
    form = await request.form()
    provider = (str(form.get("provider") or "").strip()) or None
    try:
        await start_analysis(session, ticker, user_id=user.id, provider=provider)
    except (ValueError, RuntimeError):
        pass
    return RedirectResponse(url=f"/securities/{ticker}", status_code=303)


@router.get("/tech_analysis/{analysis_id}")
async def tech_analysis_page(
    analysis_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    context = await _base_context(session, user)
    row = await session.get(TechAnalysis, analysis_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Анализ не найден")
    security = await session.scalar(
        select(Security).where(Security.ticker == row.ticker)
    )
    scenarios = {"a": {}, "b": {}, "c": {}}
    import json as _json

    if row.scenario_json:
        try:
            data = _json.loads(row.scenario_json)
            scenarios = {
                "a": data.get("scenario_a") or {},
                "b": data.get("scenario_b") or {},
                "c": data.get("scenario_c") or {},
            }
        except (ValueError, TypeError):
            scenarios = {"a": {}, "b": {}, "c": {}}
    provider = (row.provider or "").lower()
    model = row.model or ""
    llm_name = (
        "DeepSeek" if provider == "deepseek" or "deepseek" in model
        else "ChatGPT" if provider == "chatgpt" or "gpt" in model.lower()
        else provider or ""
    )
    context.update(
        {
            "analysis": row,
            "security_name": security.name if security else row.ticker,
            "scenarios": scenarios,
            "llm_name": llm_name,
            "response_html": render_markdown(row.response_md),
        }
    )
    return templates.TemplateResponse(request, "tech_analysis.html", context)


@router.post("/tech_analysis/{analysis_id}/retry")
async def tech_analysis_retry_form(
    analysis_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    try:
        await retry_analysis(analysis_id)
    except (KeyError, RuntimeError):
        pass
    return RedirectResponse(
        url=f"/tech_analysis/{analysis_id}", status_code=303
    )


@router.post("/tech_analysis/{analysis_id}/delete")
async def tech_analysis_delete_form(
    analysis_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    row = await session.get(TechAnalysis, analysis_id)
    redirect = "/"
    if row is not None:
        redirect = f"/securities/{row.ticker}"
        await session.delete(row)
        await session.commit()
    return RedirectResponse(url=redirect, status_code=303)


@router.get("/top5")
async def top5_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
    template_id: int | None = None,
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    from app.tech_analysis.batch import list_batches, top5 as top5_service

    stock_templates = (
        await session.scalars(
            select(FuturesTemplate)
            .where(FuturesTemplate.kind == "stock")
            .order_by(FuturesTemplate.name)
        )
    ).all()
    context = await _base_context(session, user)
    result = None
    selected = None
    batches = []
    if template_id is not None:
        try:
            selected = await session.get(FuturesTemplate, template_id)
            result = await top5_service(session, template_id)
            batches = await list_batches(session, template_id)
        except ValueError:
            result = None
    context.update(
        {
            "templates": [
                {"id": t.id, "name": t.name, "tickers": t.tickers, "kind": t.kind}
                for t in stock_templates
            ],
            "selected_template_id": template_id,
            "selected_template": selected,
            "result": result,
            "batches": batches,
        }
    )
    _cfg = get_cfg()
    context["llm_models"] = {
        "chatgpt": _cfg.chatgpt_model,
        "deepseek": _cfg.llm_model,
    }
    return templates.TemplateResponse(request, "top5.html", context)


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
        try:
            quote = await _moex.fetch_quote(security.ticker)
        except httpx.HTTPError:
            # MOEX недоступен (офлайн/таймаут) — страница рендерится без
            # текущей цены (P&L показывается как «—»), 500 не возвращаем.
            quote = None
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


async def _user_pipeline_pref_template(
    session: AsyncSession, user_id: int
) -> FuturesTemplate | None:
    """Шаблон фьючерсов, выбранный пользователем по умолчанию для фазы 2."""
    pref = await session.get(UserPipelinePref, user_id)
    if pref is None or pref.last_futures_template_id is None:
        return None
    return await session.get(FuturesTemplate, pref.last_futures_template_id)


@router.post("/admin/pipeline/set-template")
async def admin_pipeline_set_template(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    if not _is_admin_user(user):
        raise HTTPException(status_code=403, detail="Требуются права администратора")
    form = await request.form()
    raw = str(form.get("template_id") or "").strip()
    template_id = int(raw) if raw.isdigit() else 0
    if template_id and await session.get(FuturesTemplate, template_id) is None:
        return RedirectResponse(url="/admin?error=1", status_code=303)
    pref = await session.get(UserPipelinePref, user.id)
    if pref is None:
        pref = UserPipelinePref(user_id=user.id, last_futures_template_id=template_id or None)
        session.add(pref)
    else:
        pref.last_futures_template_id = template_id or None
    await session.commit()
    return RedirectResponse(url=str(form.get("next") or "/admin"), status_code=303)


@router.get("/admin")
async def admin_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    if not _is_admin_user(user):
        raise HTTPException(status_code=403, detail="Требуются права администратора")
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
            "failed_phase": (
                _pipeline_failed_phase(run.output)
                if run.script_name == "daily_pipeline" and run.status == "failed"
                else None
            ),
        }
        for run in runs
    ]
    futures_templates = (await session.scalars(select(FuturesTemplate).order_by(FuturesTemplate.name))).all()
    realtime_cfg = await realtime_ensure_config(session)
    # Статус демона: последний запуск realtime_updater из истории
    rt_run = await session.scalar(
        select(ScriptRun)
        .where(ScriptRun.script_name == "realtime_updater")
        .order_by(ScriptRun.id.desc())
        .limit(1)
    )
    context = await _base_context(session, user)
    context.update(
        {
            "scripts": SCRIPTS,
            "runs": items,
            "templates": futures_templates,
            "busy": is_busy(),
            "daemon_busy": is_daemon_busy(),
            "error": request.query_params.get("error") == "1",
            "param_error": request.query_params.get("error") == "2",
            "tpl_name_error": request.query_params.get("error") == "3",
            "tpl_ticker_error": request.query_params.get("error") == "4",
            "busy_error": request.query_params.get("busy") == "1",
            "realtime": realtime_cfg,
            "realtime_run": rt_run,
        }
    )
    return templates.TemplateResponse(request, "admin.html", context)


@router.post("/admin/realtime/save")
async def admin_realtime_save(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    if not _is_admin_user(user):
        raise HTTPException(status_code=403, detail="Требуются права администратора")
    form = await request.form()

    def _pos_int(name: str, default: int) -> int:
        try:
            return max(1, int(str(form.get(name) or default)))
        except ValueError:
            return default

    config = await realtime_ensure_config(session)
    config.enabled = str(form.get("realtime_enabled") or "") == "on"
    config.interval_quotes_sec = _pos_int("interval_quotes_sec", 60)
    config.interval_candles_sec = _pos_int("interval_candles_sec", 300)
    config.interval_oi_sec = _pos_int("interval_oi_sec", 900)

    tpl_raw = str(form.get("futures_template_id") or "").strip()
    tpl_id = None
    if tpl_raw:
        try:
            tpl = await session.get(FuturesTemplate, int(tpl_raw))
            if tpl is not None:
                tpl_id = tpl.id
        except ValueError:
            tpl_id = None
    config.futures_template_id = tpl_id

    config.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/admin/scripts/run")
async def admin_run_script(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    if not _is_admin_user(user):
        raise HTTPException(status_code=403, detail="Требуются права администратора")
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
            if ptype == "templates":
                # Выбранный шаблон фьючерсов -> список SECID из сохранённого шаблона
                if raw:
                    template = await session.get(FuturesTemplate, int(raw)) if raw.isdigit() else None
                    param_values[flag] = template.tickers if template else raw
                else:
                    param_values[flag] = ""
            elif ptype == "text":
                param_values[flag] = raw if raw else str(default)
            else:
                try:
                    param_values[flag] = int(raw) if raw else int(default)
                except ValueError:
                    return RedirectResponse(url="/admin?error=2", status_code=303)
        # Если выбран шаблон фьючерсов — поле тикера блокируется и не передаётся
        if param_values.get("--tickers"):
            param_values["--ticker"] = ""
        # Ежедневный конвейер: если будет выполняться фаза 2 (синхронизация
        # фьючерсов), подставляем шаблон, сохранённый пользователем в Задачах
        # фазы 2 (последний выбор), даже если не передан в форме запуска.
        if script_key == "daily_pipeline" and param_values.get("--from-phase", 1) <= 2:
            tpl = await _user_pipeline_pref_template(session, user.id)
            if tpl is not None and not param_values.get("--tickers"):
                param_values["--tickers"] = tpl.tickers
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


@router.post("/admin/templates/add")
async def admin_template_add(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    if not _is_admin_user(user):
        raise HTTPException(status_code=403, detail="Требуются права администратора")
    form = await request.form()
    name = str(form.get("name") or "").strip()
    tickers = str(form.get("tickers") or "").strip()
    if not name:
        return RedirectResponse(url="/admin?error=3", status_code=303)
    tickers_csv = ",".join(
        t.strip().upper() for t in tickers.replace(";", ",").split(",") if t.strip()
    )
    if not tickers_csv:
        return RedirectResponse(url="/admin?error=4", status_code=303)
    exists = await session.scalar(
        select(FuturesTemplate).where(FuturesTemplate.name == name)
    )
    if exists is not None:
        exists.tickers = tickers_csv
    else:
        session.add(FuturesTemplate(name=name, tickers=tickers_csv))
    await session.commit()
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/admin/templates/delete")
async def admin_template_delete(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    if not _is_admin_user(user):
        raise HTTPException(status_code=403, detail="Требуются права администратора")
    form = await request.form()
    try:
        template_id = int(str(form.get("template_id") or "0"))
    except ValueError:
        template_id = 0
    template = await session.get(FuturesTemplate, template_id)
    if template is not None:
        await session.delete(template)
        await session.commit()
    return RedirectResponse(url="/admin", status_code=303)


@router.get("/admin/futures-templates")
async def admin_futures_templates_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    if not _is_admin_user(user):
        raise HTTPException(status_code=403, detail="Требуются права администратора")
    futures_tpls = (
        await session.scalars(select(FuturesTemplate).order_by(FuturesTemplate.name))
    ).all()
    context = await _base_context(session, user)
    context.update(
        {
            "templates": [
                {"id": t.id, "name": t.name, "tickers": t.tickers, "kind": t.kind}
                for t in futures_tpls
            ],
            "error": request.query_params.get("error") == "1",
            "saved": request.query_params.get("saved") == "1",
        }
    )
    return templates.TemplateResponse(request, "futures_templates.html", context)


@router.post("/admin/futures-templates/save")
async def admin_futures_templates_save(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    if not _is_admin_user(user):
        raise HTTPException(status_code=403, detail="Требуются права администратора")
    form = await request.form()
    raw_id = str(form.get("id") or "").strip()
    name = str(form.get("name") or "").strip()
    tickers_raw = str(form.get("tickers") or "").strip()
    kind = str(form.get("kind") or "").strip().lower()
    if kind not in ("stock", "futures"):
        kind = "futures"
    if not name or not tickers_raw:
        return RedirectResponse(url="/admin/futures-templates?error=1", status_code=303)
    tickers_csv = ",".join(
        t.strip().upper() for t in tickers_raw.replace(";", ",").split(",") if t.strip()
    )
    if not tickers_csv:
        return RedirectResponse(url="/admin/futures-templates?error=1", status_code=303)
    template = None
    if raw_id.isdigit():
        template = await session.get(FuturesTemplate, int(raw_id))
    if template is None:
        template = await session.scalar(
            select(FuturesTemplate).where(FuturesTemplate.name == name)
        )
    if template is None:
        template = FuturesTemplate(name=name, tickers=tickers_csv, kind=kind)
        session.add(template)
    else:
        template.name = name
        template.tickers = tickers_csv
        template.kind = kind
    await session.commit()
    return RedirectResponse(url="/admin/futures-templates?saved=1", status_code=303)


@router.post("/admin/futures-templates/delete")
async def admin_futures_templates_delete(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    if not _is_admin_user(user):
        raise HTTPException(status_code=403, detail="Требуются права администратора")
    form = await request.form()
    try:
        template_id = int(str(form.get("template_id") or "0"))
    except ValueError:
        template_id = 0
    template = await session.get(FuturesTemplate, template_id)
    if template is not None:
        await session.delete(template)
        await session.commit()
    return RedirectResponse(url="/admin/futures-templates", status_code=303)


@router.get("/admin/graph")
async def admin_graph_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
    page: int = 1,
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    if not _is_admin_user(user):
        raise HTTPException(status_code=403, detail="Требуются права администратора")
    from sqlalchemy import func

    entities = (await session.scalars(select(Entity).order_by(Entity.name))).all()
    total = (await session.scalar(select(func.count()).select_from(Influence))) or 0
    total_pages = max(1, -(-total // 10))  # ceil per_page=10
    page = max(1, min(page, total_pages))
    influences = (
        await session.scalars(
            select(Influence)
            .order_by(Influence.id.desc())
            .offset((page - 1) * 10)
            .limit(10)
        )
    ).all()
    entity_names = {e.id: e.name for e in entities}
    context = await _base_context(session, user)
    context.update(
        {
            "entities": entities,
            "influences": [
                {
                    "id": inf.id,
                    "from": entity_names.get(inf.from_entity_id, "?"),
                    "to": entity_names.get(inf.to_entity_id, "?"),
                    "direction": inf.direction,
                    "strength": inf.strength,
                    "kind": inf.kind,
                    "confidence": inf.confidence,
                    "rationale": inf.rationale,
                    "source_ref": inf.source_ref,
                }
                for inf in influences
            ],
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "result": request.query_params.get("result", ""),
            "result_ok": request.query_params.get("ok") == "1",
        }
    )
    return templates.TemplateResponse(request, "admin_graph.html", context)


@router.post("/admin/graph/add")
async def admin_graph_add(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    if not _is_admin_user(user):
        raise HTTPException(status_code=403, detail="Требуются права администратора")
    from pathlib import Path

    from starlette.datastructures import UploadFile as StarletteUploadFile

    form = await request.form()

    # PDF-файл (анализ текста → связь определяется LLM в скрипте add_research --pdf)
    pdf_file = form.get("pdf")
    if isinstance(pdf_file, StarletteUploadFile) and pdf_file.filename:
        upload_dir = Path(os.getenv("PROJECT_ROOT", str(Path(__file__).resolve().parents[2]))) / "uploads" / "pdf"
        upload_dir.mkdir(parents=True, exist_ok=True)
        safe_name = f"article_{uuid.uuid4().hex[:12]}.pdf"
        pdf_path = upload_dir / safe_name
        with open(pdf_path, "wb") as out:
            out.write(await pdf_file.read())
        param_values = {"--pdf": str(pdf_path)}
        run = ScriptRun(script_name="add_research", params={"params": param_values}, user_id=user.id)
        session.add(run)
        await session.commit()
        launch(run.id, "add_research", param_values)
        return RedirectResponse(url=f"/admin/runs/{run.id}", status_code=303)

    # Граф влияния: вставленный ASCII/текст-схема → LLM разбирает в связи
    graph_text = str(form.get("graph") or "").strip()
    if graph_text:
        upload_dir = Path(os.getenv("PROJECT_ROOT", str(Path(__file__).resolve().parents[2]))) / "uploads" / "graph"
        upload_dir.mkdir(parents=True, exist_ok=True)
        graph_path = upload_dir / f"graph_{uuid.uuid4().hex[:12]}.txt"
        with open(graph_path, "w", encoding="utf-8") as out:
            out.write(graph_text)
        param_values = {"--graph": str(graph_path)}
        run = ScriptRun(script_name="add_research", params={"params": param_values}, user_id=user.id)
        session.add(run)
        await session.commit()
        launch(run.id, "add_research", param_values)
        return RedirectResponse(url=f"/admin/runs/{run.id}", status_code=303)

    # Ссылки: несколько строк from/to/url (+ optional rationale/strength/confidence/direction/kind)
    urls = [str(v).strip() for v in form.getlist("url") if str(v or "").strip()]
    if not urls:
        return RedirectResponse(url="/admin/graph?ok=0&result=Добавьте хотя бы одну ссылку, выберите PDF или вставьте граф влияния", status_code=303)
    from_names = form.getlist("from_name")
    to_names = form.getlist("to_name")
    rationales = form.getlist("rationale")
    strength = str(form.get("strength") or "medium")
    direction = str(form.get("direction") or "positive")
    kind = str(form.get("kind") or "direct")
    try:
        confidence = float(form.get("confidence") or 0.7)
    except ValueError:
        confidence = 0.7

    rows = []
    for i, url in enumerate(urls):
        from_name = str((from_names[i] if i < len(from_names) else "") or "").strip()
        to_name = str((to_names[i] if i < len(to_names) else "") or "").strip()
        rationale = str((rationales[i] if i < len(rationales) else "") or "").strip()
        if not (from_name and to_name):
            continue
        rows.append(
            {
                "from": from_name,
                "to": to_name,
                "url": url,
                "rationale": rationale,
                "strength": strength,
                "confidence": confidence,
                "direction": direction,
                "kind": kind,
            }
        )
    if not rows:
        return RedirectResponse(url="/admin/graph?ok=0&result=Укажите from и to для каждой ссылки", status_code=303)

    # Записать в CSV во временную папку и запустить add_research --file через раннер
    upload_dir = Path(os.getenv("PROJECT_ROOT", str(Path(__file__).resolve().parents[2]))) / "uploads" / "csv"
    upload_dir.mkdir(parents=True, exist_ok=True)
    csv_path = upload_dir / f"research_{uuid.uuid4().hex[:12]}.csv"
    import csv
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["from", "to", "url", "rationale", "strength", "confidence", "direction", "kind"])
        writer.writeheader()
        writer.writerows(rows)
    param_values = {"--file": str(csv_path)}
    run = ScriptRun(script_name="add_research", params={"params": param_values}, user_id=user.id)
    session.add(run)
    await session.commit()
    launch(run.id, "add_research", param_values)
    return RedirectResponse(url=f"/admin/runs/{run.id}", status_code=303)


@router.post("/admin/graph/{influence_id}/delete")
async def admin_graph_delete(
    influence_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    if not _is_admin_user(user):
        raise HTTPException(status_code=403, detail="Требуются права администратора")
    influence = await session.get(Influence, influence_id)
    if influence is not None:
        await session.delete(influence)
        await session.commit()
    return RedirectResponse(url=request.headers.get("referer", "/admin/graph"), status_code=303)


@router.post("/admin/graph/{influence_id}/update")
async def admin_graph_update(
    influence_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    if not _is_admin_user(user):
        raise HTTPException(status_code=403, detail="Требуются права администратора")

    influence = await session.get(Influence, influence_id)
    if influence is None:
        raise HTTPException(status_code=404, detail="Связь не найдена")

    form = await request.form()
    to_entity_id = influence.to_entity_id
    from_entity_id = influence.from_entity_id
    from_name = str(form.get("from") or "").strip()
    to_name = str(form.get("to") or "").strip()
    if from_name:
        resolved = await resolve_entity_id(session, from_name)
        if resolved is None:
            raise HTTPException(status_code=400, detail=f"Сущность «{from_name}» не найдена")
        from_entity_id = resolved
    if to_name:
        resolved = await resolve_entity_id(session, to_name)
        if resolved is None:
            raise HTTPException(status_code=400, detail=f"Сущность «{to_name}» не найдена")
        to_entity_id = resolved
    influence.from_entity_id = from_entity_id
    influence.to_entity_id = to_entity_id

    direction = str(form.get("direction") or "").strip()
    if direction in ("positive", "negative"):
        influence.direction = direction
    strength = str(form.get("strength") or "").strip()
    if strength in ("weak", "medium", "strong"):
        influence.strength = strength
    kind = str(form.get("kind") or "").strip()
    if kind in ("direct", "indirect"):
        influence.kind = kind
    try:
        confidence = float(form.get("confidence") or "")
    except (TypeError, ValueError):
        confidence = influence.confidence
    if 0.0 <= confidence <= 1.0:
        influence.confidence = confidence

    rationale = str(form.get("rationale") or "").strip()
    if form.get("rationale") is not None:
        influence.rationale = rationale
    source_ref = str(form.get("source_ref") or "").strip()
    if form.get("source_ref") is not None:
        influence.source_ref = source_ref or "curated"

    await session.commit()
    return RedirectResponse(url=request.headers.get("referer", "/admin/graph"), status_code=303)


@router.get("/admin/graph/export")
async def admin_graph_export(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Скачать дамп графа (JSONL) через браузер с именем {YYYYMMDD_HHMMSS}_seed_dump.jsonl."""
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    if not _is_admin_user(user):
        raise HTTPException(status_code=403, detail="Требуются права администратора")

    entities, influences = await export_graph_records(session)
    payload = graph_to_jsonl(entities, influences)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{stamp}_seed_dump.jsonl"
    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/admin/graph/import")
async def admin_graph_import(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Загрузка JSONL-дампа графа и запуск идемпотентного импорта (scripts.import_graph --file)."""
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    if not _is_admin_user(user):
        raise HTTPException(status_code=403, detail="Требуются права администратора")

    from starlette.datastructures import UploadFile as StarletteUploadFile

    form = await request.form()
    up = form.get("file")
    if not (isinstance(up, StarletteUploadFile) and up.filename):
        return RedirectResponse(url="/admin/graph?ok=0&result=Выберите JSONL-файл дампа графа", status_code=303)

    content = (await up.read()).decode("utf-8", errors="replace").strip()
    if not content:
        return RedirectResponse(url="/admin/graph?ok=0&result=Файл пуст", status_code=303)

    upload_dir = Path(os.getenv("PROJECT_ROOT", str(Path(__file__).resolve().parents[2]))) / "uploads" / "graph"
    upload_dir.mkdir(parents=True, exist_ok=True)
    dump_path = upload_dir / f"import_graph_{uuid.uuid4().hex[:12]}.jsonl"
    with open(dump_path, "w", encoding="utf-8") as f:
        f.write(content)

    param_values = {"--file": str(dump_path)}
    run = ScriptRun(script_name="import_graph", params={"params": param_values}, user_id=user.id)
    session.add(run)
    await session.commit()
    launch(run.id, "import_graph", param_values)
    return RedirectResponse(url=f"/admin/runs/{run.id}", status_code=303)


_PIPELINE_TITLES = {
    1: "Новости",
    2: "Свечи MOEX",
    3: "Стратегии",
    4: "Алерты",
    5: "Виртуальный портфель",
}

# Подзадачи фаз: (иконка, название, описание, маркер в логе для «выполнено»)
_PIPELINE_TASKS = {
    1: [
        ("📡", "Сбор RSS новостей", "Получение новостей из RSS-лент финансовых и экономических ресурсов", "Новости:"),
        ("✈️", "Сбор Telegram новостей", "Сообщения из Telegram-каналов и чатов", "Telegram-новости:"),
        ("🌐", "Сбор новостей с сайтов", "Парсинг новостей с сайтов СМИ и финансовых порталов", "Сайты:"),
    ],
    2: [
        ("📈", "Синхронизация акций", "Обновление котировок и свечей акций (TQBR)", "Синхронизация акций:"),
        ("📊", "Синхронизация фьючерсов", "Обновление контрактов срочного рынка (FORTS)", "Синхронизация фьючерсов:"),
    ],
    3: [
        ("🎯", "Генерация стратегий", "Анализ данных, поиск паттернов и генерация стратегий", "strategies stored:"),
    ],
    4: [
        ("🔔", "Генерация алертов", "Формирование алертов по найденным стратегиям", "Алерты:"),
        ("✉️", "Отправка алертов в Telegram", "Push-уведомления в Telegram-каналы и чаты", "Telegram: отправлено"),
    ],
    5: [
        ("📥", "Покупка ценных бумаг", "Открытие виртуальных позиций на покупку", "открыто"),
        ("📤", "Продажа ценных бумаг", "Закрытие виртуальных позиций и фиксация прибыли", "закрыто"),
    ],
}

# Иконки фаз для шапки дашборда
_PIPELINE_ICONS = {1: "📰", 2: "🗄️", 3: "🎯", 4: "🔔", 5: "💼"}


def _log_line(output: str, marker: str) -> str | None:
    """Хвост строки лога, содержащей маркер (например «5 сохранено» из «Новости: 5 сохранено»)."""
    for line in output.splitlines():
        if marker in line:
            tail = line.split(marker, 1)[1].strip()
            return tail or None
    return None


def _task_state(phase_state: str, done: bool, index: int, has_marker: bool, active_idx: int | None = None) -> str:
    """Состояние подзадачи по состоянию фазы и наличию маркера завершения в логе."""
    if phase_state == "done":
        return "done" if (done or not has_marker) else "waiting"
    if phase_state == "running":
        if done:
            return "done"
        return "running" if active_idx == index else "waiting"
    if phase_state == "error":
        return "done" if done else "waiting"
    return "waiting"


def _pipeline_phases(output: str | None, status: str | None) -> list[dict] | None:
    """Состояния 5 фаз Ежедневного конвейера по логу: done/skipped/running/error/pending,
    с подзадачами (tasks) и процентом выполнения фазы."""
    if not output or "Фаза " not in output:
        return None
    skipped = None
    m = re.search(r"Пропускаю фазы (\d+)\.\.(\d+)", output)
    if m:
        skipped = int(m.group(2))
    started = [int(x) for x in re.findall(r"Фаза (\d+)/5:", output)]
    started = [x for x in started if 1 <= x <= 5]
    if not started and skipped is None:
        return None
    last = started[-1] if started else 0
    phases = []
    for n in range(1, 6):
        if skipped is not None and n <= skipped:
            state = "skipped"
        elif n < last:
            state = "done"
        elif n == last:
            if status == "failed":
                state = "error"
            elif status == "success":
                state = "done"
            else:
                state = "running"
        else:
            state = "pending"
        tasks = []
        done_count = 0
        # Фаза 1: вспомогательные задачи (Telegram/сайты) показываем, только если
        # соответствующий флаг был передан (в логе есть «Фаза 1b/5»/«Фаза 1c/5»).
        tpl_tasks = _PIPELINE_TASKS.get(n, [])
        if n == 1:
            tpl_tasks = [
                t for t in tpl_tasks
                if t[1] != "Сбор Telegram новостей" or "Фаза 1b/5:" in output
            ]
            tpl_tasks = [
                t for t in tpl_tasks
                if t[1] != "Сбор новостей с сайтов" or "Фаза 1c/5:" in output
            ]
        undones = []  # индексы ещё не выполненных подзадач (для текущей при running)
        task_done = []
        for i, (icon, t_title, t_desc, marker) in enumerate(tpl_tasks):
            done = bool(marker and marker in output)
            task_done.append(done)
            if done:
                done_count += 1
            else:
                undones.append(i)
        active_idx = undones[0] if undones else None
        for i, (icon, t_title, t_desc, marker) in enumerate(tpl_tasks):
            done = task_done[i]
            t_state = _task_state(state, done, i, marker is not None, active_idx)
            count = None
            if done and marker:
                num = re.search(rf"{re.escape(marker)}\s*(\d+)", output)
                count = num.group(1) if num else None
            tasks.append(
                {"icon": icon, "title": t_title, "desc": t_desc, "state": t_state, "count": count}
            )
        total = len(tasks) or 1
        pct = round(done_count / total * 100) if state != "pending" else 0
        phases.append(
            {
                "n": n,
                "title": _PIPELINE_TITLES.get(n, str(n)),
                "icon": _PIPELINE_ICONS.get(n, "📌"),
                "state": state,
                "tasks": tasks,
                "pct": pct,
            }
        )
    return phases


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
    partial: bool = False,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    if not _is_admin_user(user):
        raise HTTPException(status_code=403, detail="Требуются права администратора")
    run = await session.get(ScriptRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Запуск не найден")
    pipeline = _pipeline_phases(run.output, run.status) if run.script_name == "daily_pipeline" else None
    active_phase = None
    if pipeline:
        for ph in pipeline:
            if ph["state"] in ("running", "error"):
                active_phase = ph
                break
        if active_phase is None and pipeline[-1]["state"] in ("done", "skipped"):
            active_phase = pipeline[-1]
    futures_templates = (
        await session.scalars(select(FuturesTemplate).order_by(FuturesTemplate.name))
    ).all()
    user_template = await _user_pipeline_pref_template(session, user.id)
    context = await _base_context(session, user)
    context.update(
        {
            "run": run,
            "script_title": (get_script(run.script_name) or {}).get("title", run.script_name),
            "running": run.status == "running",
            "progress": _run_progress(run.output),
            "pipeline": pipeline,
            "active_phase": active_phase,
            "templates": futures_templates,
            "user_template_id": user_template.id if user_template else None,
            "user_template_tickers": user_template.tickers if user_template else "",
        }
    )
    template = "admin_run_partial.html" if partial else "admin_run.html"
    return templates.TemplateResponse(request, template, context)


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
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    state = await notice_state(session)
    state["can_dismiss"] = await _optional_user(request, session) is not None
    return state


@router.post("/api/notices/{notice_id}/dismiss")
async def dismiss_notice_api(
    notice_id: int,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if not await dismiss_notice(session, notice_id):
        raise HTTPException(status_code=404, detail="Уведомление не найдено")
    return {"dismissed": 1}


@router.post("/api/notices/dismiss-all")
async def dismiss_all_notices_api(
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return {"dismissed": await dismiss_all_notices(session)}


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
    sites = await user_sources(session, user.id, kind="website")
    context = await _base_context(session, user)
    context.update(
        {
            "feeds": feeds,
            "sites": sites,
            "categories": SOURCE_CATEGORIES,
            "error": request.query_params.get("error", ""),
            "info": request.query_params.get("info", ""),
            "candidates": [],
            "tab": request.query_params.get("tab", "rss"),
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
    err = await validate_feed_url(url)
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


@router.post("/news/rss/toggle")
async def news_rss_toggle(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Включает/выключает флаг ленты: use_llm (LLM-разбор) или use_browser (обход антибота)."""
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    form = await request.form()
    try:
        source_id = int(str(form.get("source_id") or "0"))
    except ValueError:
        source_id = 0
    field = str(form.get("field") or "")
    on = str(form.get("on") or "0") == "1"
    source = await session.scalar(
        select(Source)
        .join(UserSource, UserSource.source_id == Source.id)
        .where(UserSource.user_id == user.id, Source.id == source_id)
    )
    if source is not None:
        if field == "use_llm":
            source.use_llm = on
        elif field == "use_browser":
            source.use_browser = on
        if on:
            url = (source.config or {}).get("url") or ""
            if url:
                from app.news.feed_check import check_feed

                ok, desc = await check_feed(
                    url,
                    use_llm=bool(source.use_llm),
                    use_browser=bool(source.use_browser),
                )
                source.last_status = "ok" if ok else "error"
                source.last_error = "" if ok else desc
                source.last_checked_at = datetime.now(timezone.utc)
        await session.commit()
    return RedirectResponse(url="/news", status_code=303)


@router.post("/news/rss/update")
async def news_rss_update(
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
    url = str(form.get("url") or "").strip()
    category = str(form.get("category") or "").strip()
    reputation_raw = str(form.get("reputation") or "").strip()
    if not url.startswith(("http://", "https://")):
        return RedirectResponse(url="/news?error=Некорректный URL", status_code=303)
    try:
        reputation = float(reputation_raw)
    except ValueError:
        return RedirectResponse(url="/news?error=Некорректная репутация", status_code=303)
    if not 0 <= reputation <= 1:
        return RedirectResponse(url="/news?error=Репутация должна быть от 0 до 1", status_code=303)
    source = await session.scalar(
        select(Source)
        .join(UserSource, UserSource.source_id == Source.id)
        .where(UserSource.user_id == user.id, Source.id == source_id)
    )
    if source is None:
        return RedirectResponse(url="/news?error=Источник не найден", status_code=303)
    if category and category not in SOURCE_CATEGORIES and category != (source.category or ""):
        return RedirectResponse(url="/news?error=Недопустимая категория", status_code=303)
    old_url = (source.config or {}).get("url")
    config = dict(source.config or {})
    config["url"] = url
    source.config = config
    source.category = category or None
    source.reputation_score = reputation
    if old_url != url:
        source.last_status = None
        source.last_error = None
    await session.commit()
    return RedirectResponse(url="/news?info=Лента обновлена", status_code=303)


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
    try:
        count = min(int(str(form.get("cand_count") or "0")), 50)
    except ValueError:
        count = 0
    added = 0
    for index in range(count):
        if form.get(f"cand_{index}_pick") is None:
            continue
        name = str(form.get(f"cand_{index}_name") or "").strip()
        url = str(form.get(f"cand_{index}_url") or "").strip()
        category = str(form.get(f"cand_{index}_category") or "")
        if name and url and not await validate_feed_url(url):
            try:
                await add_source_api(
                    SourceIn(name=name, url=url, category=category), user, session
                )
                added += 1
            except HTTPException:
                pass
    return RedirectResponse(
        url=f"/news?info=Добавлено лент: {added}", status_code=303
    )


@router.post("/news/site/add")
async def news_site_add(
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
        return RedirectResponse(
            url="/news?tab=sites&error=Заполните имя и URL", status_code=303
        )
    if category not in SOURCE_CATEGORIES and category != "":
        return RedirectResponse(
            url="/news?tab=sites&error=Недопустимая категория", status_code=303
        )
    err = await validate_feed_url(url)
    if err:
        return RedirectResponse(url=f"/news?tab=sites&error={err}", status_code=303)
    try:
        await add_source_api(
            SourceIn(name=name, url=url, category=category, kind="website"),
            user,
            session,
        )
    except HTTPException as exc:
        return RedirectResponse(
            url=f"/news?tab=sites&error={exc.detail}", status_code=303
        )
    return RedirectResponse(url="/news?tab=sites", status_code=303)


@router.post("/news/site/remove")
async def news_site_remove(
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
    return RedirectResponse(url="/news?tab=sites", status_code=303)


@router.post("/news/site/toggle")
async def news_site_toggle(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Включает/выключает флаг сайта: use_llm или use_browser."""
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    form = await request.form()
    try:
        source_id = int(str(form.get("source_id") or "0"))
    except ValueError:
        source_id = 0
    field = str(form.get("field") or "")
    on = str(form.get("on") or "0") == "1"
    source = await session.scalar(
        select(Source)
        .join(UserSource, UserSource.source_id == Source.id)
        .where(UserSource.user_id == user.id, Source.id == source_id)
    )
    if source is not None:
        if field == "use_llm":
            source.use_llm = on
        elif field == "use_browser":
            source.use_browser = on
        if on:
            url = (source.config or {}).get("url") or ""
            if url:
                ok, desc = await check_website(
                    url,
                    use_llm=bool(source.use_llm),
                    use_browser=bool(source.use_browser),
                )
                source.last_status = "ok" if ok else "error"
                source.last_error = "" if ok else desc
                source.last_checked_at = datetime.now(timezone.utc)
        await session.commit()
    return RedirectResponse(url="/news?tab=sites", status_code=303)


@router.post("/news/site/update")
async def news_site_update(
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
    url = str(form.get("url") or "").strip()
    category = str(form.get("category") or "").strip()
    reputation_raw = str(form.get("reputation") or "").strip()
    if not url.startswith(("http://", "https://")):
        return RedirectResponse(
            url="/news?tab=sites&error=Некорректный URL", status_code=303
        )
    try:
        reputation = float(reputation_raw)
    except ValueError:
        return RedirectResponse(
            url="/news?tab=sites&error=Некорректная репутация", status_code=303
        )
    if not 0 <= reputation <= 1:
        return RedirectResponse(
            url="/news?tab=sites&error=Репутация должна быть от 0 до 1", status_code=303
        )
    source = await session.scalar(
        select(Source)
        .join(UserSource, UserSource.source_id == Source.id)
        .where(UserSource.user_id == user.id, Source.id == source_id)
    )
    if source is None:
        return RedirectResponse(
            url="/news?tab=sites&error=Источник не найден", status_code=303
        )
    if category and category not in SOURCE_CATEGORIES and category != (source.category or ""):
        return RedirectResponse(
            url="/news?tab=sites&error=Недопустимая категория", status_code=303
        )
    old_url = (source.config or {}).get("url")
    config = dict(source.config or {})
    config["url"] = url
    source.config = config
    source.category = category or None
    source.reputation_score = reputation
    if old_url != url:
        source.last_status = None
        source.last_error = None
    await session.commit()
    return RedirectResponse(url="/news?tab=sites&info=Сайт обновлён", status_code=303)


@router.post("/news/site/check")
async def news_site_check(
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
        return RedirectResponse(
            url="/news?tab=sites&error=Не удалось проверить сайты", status_code=303
        )
    return RedirectResponse(
        url=f"/news?tab=sites&info=Проверено сайтов: {len(ids) if ids else 'все'}",
        status_code=303,
    )


@router.post("/news/site/restore")
async def news_site_restore(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _optional_user(request, session)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    added = await restore_default_sites(session, user.id)
    return RedirectResponse(
        url=f"/news?tab=sites&info=Добавлено стандартных сайтов: {added}",
        status_code=303,
    )
