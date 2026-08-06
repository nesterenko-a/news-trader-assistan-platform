from datetime import date
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.connection import get_session
from app.db.models import MarketCandle, MarketOpenPosition, Security
from app.market.indicators.base import IndicatorResult
from app.market.indicators.oi import calculate_oi
from app.market.indicators.registry import REGISTRY
from app.market.indicators.volume_profile import calculate_volume_profile
from app.market.indicators.ema import calculate_ema
from app.market.indicators.macd import calculate_macd
from app.market.moex import MOEXClient

router = APIRouter(prefix="/indicators", tags=["indicators"])

_FUTURES_CACHE: dict = {"ts": 0.0, "data": None}
_FUTURES_TTL_SECONDS = 3600


@router.get("/futures")
async def list_futures(q: str | None = None) -> dict:
    """Все фьючерсы срочного рынка MOEX (для загрузки OI), с TTL-кэшем 1 час."""
    now = time.monotonic()
    if _FUTURES_CACHE["data"] is None or now - _FUTURES_CACHE["ts"] > _FUTURES_TTL_SECONDS:
        _FUTURES_CACHE["data"] = await MOEXClient().fetch_futures_list()
        _FUTURES_CACHE["ts"] = now
    futures = _FUTURES_CACHE["data"]
    if q:
        needle = q.strip().lower()
        futures = [
            f
            for f in futures
            if needle in f["secid"].lower()
            or needle in f["shortname"].lower()
            or needle in f["assetcode"].lower()
        ]
    return {"count": len(futures), "futures": futures}


@router.get("")
async def list_indicators() -> dict:
    return {
        "indicators": [
            {"name": name, **meta} for name, meta in sorted(REGISTRY.items())
        ]
    }


@router.get("/{name}")
async def calculate_indicator(
    name: str,
    ticker: str = Query(...),
    from_: date | None = Query(None, alias="from"),
    to: date | None = Query(None, alias="to"),
    limit: int | None = Query(None, ge=1, le=5000),
    oi_change_threshold_pct: float | None = Query(None, gt=0),
    price_change_threshold_pct: float | None = Query(None, ge=0),
    period: int | None = Query(None, ge=5, le=5000),
    bins: int | None = Query(None, ge=10, le=500),
    value_area_pct: float | None = Query(None, gt=0, le=100),
    hvn_factor: float | None = Query(None, gt=1),
    lvn_factor: float | None = Query(None, gt=0, lt=1),
    fast: int | None = Query(None, ge=2, le=500),
    slow: int | None = Query(None, ge=3, le=500),
    signal: int | None = Query(None, ge=2, le=100),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if name not in REGISTRY:
        raise HTTPException(status_code=404, detail="Индикатор не найден")

    security = await session.scalar(
        select(Security).where(Security.ticker == ticker.upper())
    )
    if security is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Бумага не найдена. Для фьючерсных контрактов сначала скачайте "
                "данные OI: python -m scripts.update_oi --ticker <SECID> "
                "(например W4V6)"
            ),
        )

    if name == "oi":
        return _result_to_dict(
            await _calculate_oi(
                session,
                security,
                from_date=from_,
                till_date=to,
                limit=limit,
                params={
                    "oi_change_threshold_pct": oi_change_threshold_pct,
                    "price_change_threshold_pct": price_change_threshold_pct,
                },
            )
        )

    if name == "volume_profile":
        candle_q = (
            select(MarketCandle)
            .where(MarketCandle.security_id == security.id)
            .order_by(MarketCandle.trading_date)
        )
        candles = (await session.scalars(candle_q)).all()
        if limit is not None:
            candles = candles[-limit:]
        return _result_to_dict(
            calculate_volume_profile(
                candles,
                params={
                    "period": period,
                    "bins": bins,
                    "value_area_pct": value_area_pct,
                    "hvn_factor": hvn_factor,
                    "lvn_factor": lvn_factor,
                },
            )
        )

    if name in ("ema", "macd"):
        if fast is not None and slow is not None and fast >= slow:
            raise HTTPException(status_code=422, detail="fast должен быть меньше slow")
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
        candles = (await session.scalars(candle_q)).all()
        if limit is not None:
            candles = candles[-limit:]
        params = {"fast": fast, "slow": slow, "signal": signal}
        if name == "ema":
            return _result_to_dict(calculate_ema(candles, params=params))
        return _result_to_dict(calculate_macd(candles, params=params))
    raise HTTPException(status_code=404, detail="Индикатор не найден")


async def _calculate_oi(
    session: AsyncSession,
    security: Security,
    from_date: date | None,
    till_date: date | None,
    limit: int | None,
    params: dict,
) -> IndicatorResult:
    candle_q = select(MarketCandle).where(MarketCandle.security_id == security.id)
    oi_q = select(MarketOpenPosition).where(
        MarketOpenPosition.security_id == security.id
    )
    if from_date is not None:
        candle_q = candle_q.where(MarketCandle.trading_date >= from_date)
        oi_q = oi_q.where(MarketOpenPosition.trading_date >= from_date)
    if till_date is not None:
        candle_q = candle_q.where(MarketCandle.trading_date <= till_date)
        oi_q = oi_q.where(MarketOpenPosition.trading_date <= till_date)
    candle_q = candle_q.order_by(MarketCandle.trading_date)
    oi_q = oi_q.order_by(MarketOpenPosition.trading_date)

    candles = (await session.scalars(candle_q)).all()
    oi_rows = (await session.scalars(oi_q)).all()
    if limit is not None:
        candles = candles[-limit:]
        oi_rows = oi_rows[-limit:]

    close_by_date = {c.trading_date: c.close for c in candles}
    oi_by_date = {r.trading_date: r.open_position for r in oi_rows}

    if not oi_by_date:
        raise HTTPException(
            status_code=400,
            detail="Нет данных по открытым позициям: запустите scripts/update_oi.py --ticker <SECID>",
        )

    dates = sorted(set(close_by_date) | set(oi_by_date))
    series = [(d, close_by_date.get(d), oi_by_date.get(d)) for d in dates]

    effective_params = {k: v for k, v in params.items() if v is not None}
    return calculate_oi(series, params=effective_params)


def _result_to_dict(result: IndicatorResult) -> dict:
    return {
        "indicator": result.indicator,
        "params": result.params,
        "values": [
            {"date": v.date.isoformat(), "kind": v.kind, "value": v.value}
            for v in result.values
        ],
        "signals": [
            {
                "date": s.date.isoformat(),
                "kind": s.kind,
                "severity": s.severity,
                "note": s.note,
                "volume": s.volume,
            }
            for s in result.signals
        ],
        "meta": result.meta,
    }
