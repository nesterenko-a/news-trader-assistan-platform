"""ATR — средний истинный диапазон (docs/19 §8.8).

TRₜ = max(Hₜ − Lₜ, |Hₜ − Cₜ₋₁|, |Lₜ − Cₜ₋₁|)
ATR — сглаживание Уайлдера (n = 14): ATRₜ = (ATRₜ₋₁·(n−1) + TRₜ) / n;
seed — простое среднее первых n TR.

Значение волатильности; сигналов сам по себе не даёт.
"""

from app.market.indicators.base import IndicatorResult, IndicatorValue

DEFAULT_PARAMS = {
    "period": 14,
}


def _candle_date(candle):
    return getattr(candle, "date", None) or getattr(candle, "trading_date", None)


def calculate_atr(
    candles: list,
    params: dict | None = None,
) -> IndicatorResult:
    """ATR по свечам (high/low/close) со сглаживанием Уайлдера.

    candles — список объектов с атрибутами date (или trading_date),
    high, low и close.
    """
    p = {**DEFAULT_PARAMS}
    for key, value in (params or {}).items():
        if value is not None:
            p[key] = value
    period = int(p["period"])
    if period <= 0:
        return IndicatorResult(
            indicator="atr",
            params=p,
            values=[],
            signals=[],
            meta={"note": "некорректные параметры"},
        )

    valid = [
        c
        for c in candles
        if getattr(c, "close", None) is not None
        and getattr(c, "high", None) is not None
        and getattr(c, "low", None) is not None
    ]
    dates = [_candle_date(c) for c in valid]

    empty = IndicatorResult(
        indicator="atr",
        params=p,
        values=[],
        signals=[],
        meta={"note": "недостаточно данных для ATR (нужны high/low/close)"},
    )
    if len(valid) < period + 1:
        return empty

    tr_list: list[float] = []
    for i in range(1, len(valid)):
        h, l, prev_c = valid[i].high, valid[i].low, valid[i - 1].close
        tr_list.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))

    # seed: простое среднее первых period TR
    n = len(valid)
    atr_by_candle: list[float | None] = [None] * n
    seed_atr = sum(tr_list[:period]) / period
    atr_by_candle[period] = seed_atr
    for k in range(period + 1, n):
        seed_atr = (seed_atr * (period - 1) + tr_list[k - 1]) / period
        atr_by_candle[k] = seed_atr

    # ATR на дату свечи k (k ≥ period) опирается на TR по свечам 1..k
    values: list[IndicatorValue] = [
        IndicatorValue(date=d, value=round(av, 4), kind="atr")
        for d, av in zip(dates, atr_by_candle)
        if av is not None
    ]

    last_atr = values[-1].value if values else None
    last_close = valid[-1].close
    atr_pct = (100.0 * last_atr / last_close) if last_atr is not None and last_close else None

    return IndicatorResult(
        indicator="atr",
        params=p,
        values=values,
        signals=[],
        meta={
            "period": period,
            "latest_atr": round(last_atr, 4) if last_atr is not None else None,
            "atr_pct": round(atr_pct, 4) if atr_pct is not None else None,
            "last_close": round(last_close, 4),
            "candles": len(valid),
            "from": dates[0].isoformat(),
            "to": dates[-1].isoformat(),
        },
    )
