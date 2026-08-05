from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.connection import get_session
from app.db.models import MarketCandle, MarketOpenPosition, Security
from app.market.indicators.base import IndicatorResult
from app.market.indicators.oi import calculate_oi
from app.market.indicators.registry import REGISTRY

router = APIRouter(prefix="/indicators", tags=["indicators"])


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
    session: AsyncSession = Depends(get_session),
) -> dict:
    if name not in REGISTRY:
        raise HTTPException(status_code=404, detail="Индикатор не найден")

    security = await session.scalar(
        select(Security).where(Security.ticker == ticker.upper())
    )
    if security is None:
        raise HTTPException(status_code=400, detail="Бумага не найдена")

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
            }
            for s in result.signals
        ],
        "meta": result.meta,
    }
