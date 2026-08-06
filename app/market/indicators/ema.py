"""EMA — экспоненциальная скользящая средняя (docs/19 §8.1).

α = 2 / (N + 1); seed: EMA первых N значений = SMA(N);
далее EMAₜ = Cₜ·α + EMAₜ₋₁·(1−α).
Сигналы: пересечение ema_fast и ema_slow — cross_up / cross_down.
"""

from app.market.indicators.base import IndicatorResult, IndicatorSignal, IndicatorValue

DEFAULT_PARAMS = {
    "fast": 12,
    "slow": 26,
}


def _candle_date(candle):
    return getattr(candle, "date", None) or getattr(candle, "trading_date", None)


def _ema(values: list[float], period: int) -> list[float | None]:
    """EMA по ряду цен; None для первых period-1 точек (нет seed)."""
    if period <= 0 or len(values) < period:
        return [None] * len(values)
    alpha = 2.0 / (period + 1)
    out: list[float | None] = [None] * (period - 1)
    ema = sum(values[:period]) / period
    out.append(ema)
    for price in values[period:]:
        ema = price * alpha + ema * (1 - alpha)
        out.append(ema)
    return out


def calculate_ema(
    candles: list,
    params: dict | None = None,
) -> IndicatorResult:
    """EMA(fast)/EMA(slow) по свечам и сигналы пересечения.

    candles — список объектов с атрибутами date (или trading_date) и close.
    """
    p = {**DEFAULT_PARAMS}
    for key, value in (params or {}).items():
        if value is not None:
            p[key] = value
    fast = int(p["fast"])
    slow = int(p["slow"])

    dates = [_candle_date(c) for c in candles]
    closes = [c.close for c in candles]

    fast_series = _ema(closes, fast)
    slow_series = _ema(closes, slow)

    empty = IndicatorResult(
        indicator="ema",
        params=p,
        values=[],
        signals=[],
        meta={"note": "недостаточно данных для EMA"},
    )
    if not dates or len(closes) < slow + 1:
        return empty

    values: list[IndicatorValue] = []
    signals: list[IndicatorSignal] = []
    prev_fast = prev_slow = None
    for i, d in enumerate(dates):
        f, s = fast_series[i], slow_series[i]
        if f is not None:
            values.append(IndicatorValue(date=d, value=round(f, 4), kind="ema_fast"))
        if s is not None:
            values.append(IndicatorValue(date=d, value=round(s, 4), kind="ema_slow"))
        if (
            f is not None
            and s is not None
            and prev_fast is not None
            and prev_slow is not None
        ):
            if prev_fast <= prev_slow and f > s:
                signals.append(
                    IndicatorSignal(
                        date=d,
                        kind="cross_up",
                        severity="strong",
                        note=(
                            f"EMA({fast}) пересекла EMA({slow}) снизу вверх — "
                            "бычий сигнал (golden cross)"
                        ),
                    )
                )
            elif prev_fast >= prev_slow and f < s:
                signals.append(
                    IndicatorSignal(
                        date=d,
                        kind="cross_down",
                        severity="strong",
                        note=(
                            f"EMA({fast}) пересекла EMA({slow}) сверху вниз — "
                            "медвежий сигнал (death cross)"
                        ),
                    )
                )
        prev_fast, prev_slow = f, s

    last_fast = next((v.value for v in reversed(values) if v.kind == "ema_fast"), None)
    last_slow = next((v.value for v in reversed(values) if v.kind == "ema_slow"), None)

    return IndicatorResult(
        indicator="ema",
        params=p,
        values=values,
        signals=signals,
        meta={
            "fast": fast,
            "slow": slow,
            "latest_fast": round(last_fast, 4) if last_fast is not None else None,
            "latest_slow": round(last_slow, 4) if last_slow is not None else None,
            "trend": (
                "up" if last_fast is not None and last_slow is not None and last_fast > last_slow
                else "down" if last_fast is not None and last_slow is not None
                else "unknown"
            ),
            "candles": len(closes),
            "from": dates[0].isoformat(),
            "to": dates[-1].isoformat(),
        },
    )
