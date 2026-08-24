"""Полосы Боллинджера (docs/19 §8.3).

middle = SMA(N); upper = middle + k·σ; lower = middle − k·σ;
%B = (C − lower) / (upper − lower); bandwidth = (upper − lower) / middle.

Сигналы: touch_upper / touch_lower, revert_in, squeeze (bandwidth ниже
10-го процентиля за окно).
"""

from app.market.indicators.base import IndicatorResult, IndicatorSignal, IndicatorValue

DEFAULT_PARAMS = {
    "period": 20,
    "k": 2,
}


def _candle_date(candle):
    return getattr(candle, "date", None) or getattr(candle, "trading_date", None)


def _sma(values: list[float], period: int) -> list[float | None]:
    """Простое скользящее среднее; None для первых period-1 точек."""
    if period <= 0 or len(values) < period:
        return [None] * len(values)
    out: list[float | None] = [None] * (period - 1)
    running = sum(values[:period])
    out.append(running / period)
    for i in range(period, len(values)):
        running += values[i] - values[i - period]
        out.append(running / period)
    return out


def _rolling_std(values: list[float], period: int) -> list[float | None]:
    """Популяционное стандартное отклонение за окно (σ по §8.3)."""
    if period <= 0 or len(values) < period:
        return [None] * len(values)
    out: list[float | None] = [None] * (period - 1)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        mean = sum(window) / period
        var = sum((x - mean) ** 2 for x in window) / period
        out.append(var ** 0.5)
    return out


def calculate_bollinger(
    candles: list,
    params: dict | None = None,
) -> IndicatorResult:
    """Полосы Боллинджера по свечам (close) + %B + bandwidth + сигналы.

    candles — список объектов с атрибутами date (или trading_date) и close.
    """
    p = {**DEFAULT_PARAMS}
    for key, value in (params or {}).items():
        if value is not None:
            p[key] = value
    period = int(p["period"])
    k = float(p["k"])
    if period <= 0 or k <= 0:
        return IndicatorResult(
            indicator="bollinger",
            params=p,
            values=[],
            signals=[],
            meta={"note": "некорректные параметры"},
        )

    valid = [(c, c.close) for c in candles if getattr(c, "close", None) is not None]
    dates = [_candle_date(c) for c, _ in valid]
    closes = [close for _, close in valid]

    empty = IndicatorResult(
        indicator="bollinger",
        params=p,
        values=[],
        signals=[],
        meta={"note": "недостаточно данных для Боллинджера"},
    )
    if not dates or len(closes) < period:
        return empty

    middle = _sma(closes, period)
    std = _rolling_std(closes, period)
    upper = [
        (m + k * s) if m is not None and s is not None else None
        for m, s in zip(middle, std)
    ]
    lower = [
        (m - k * s) if m is not None and s is not None else None
        for m, s in zip(middle, std)
    ]

    # Squeeze: bandwidth ниже 10-го процентиля за окно (по available значениям)
    bandwidth_vals: list[float] = []
    for m, u, l in zip(middle, upper, lower):
        if m and u is not None and l is not None and m != 0:
            bandwidth_vals.append((u - l) / m)
    squeeze_threshold = None
    if len(bandwidth_vals) >= period:
        sorted_bw = sorted(bandwidth_vals)
        squeeze_threshold = sorted_bw[max(0, int(len(sorted_bw) * 0.10) - 1)]

    values: list[IndicatorValue] = []
    signals: list[IndicatorSignal] = []
    prev_close = None
    for i, d in enumerate(dates):
        c = closes[i]
        m, u, l = middle[i], upper[i], lower[i]
        if m is not None:
            values.append(IndicatorValue(date=d, value=round(m, 4), kind="middle"))
        if u is not None:
            values.append(IndicatorValue(date=d, value=round(u, 4), kind="upper"))
        if l is not None:
            values.append(IndicatorValue(date=d, value=round(l, 4), kind="lower"))
        if u is not None and l is not None and u != l:
            percent_b = (c - l) / (u - l)
            bandwidth = (u - l) / m if m else 0.0
            values.append(IndicatorValue(date=d, value=round(percent_b, 4), kind="percent_b"))
            # touch / revert по переходу цены через полосу
            if prev_close is not None:
                if prev_close <= u < c:
                    signals.append(
                        IndicatorSignal(
                            date=d,
                            kind="touch_upper",
                            severity="warning",
                            note=(
                                f"цена {c:.2f} пересекла верхнюю полосу "
                                f"{u:.2f} — перекупленность"
                            ),
                        )
                    )
                elif prev_close >= l > c:
                    signals.append(
                        IndicatorSignal(
                            date=d,
                            kind="touch_lower",
                            severity="warning",
                            note=(
                                f"цена {c:.2f} пересекла нижнюю полосу "
                                f"{l:.2f} — перепроданность"
                            ),
                        )
                    )
                if prev_close > u >= c or (prev_close < l <= c):
                    signals.append(
                        IndicatorSignal(
                            date=d,
                            kind="revert_in",
                            severity="info",
                            note="возврат цены внутрь полос Боллинджера",
                        )
                    )
            if squeeze_threshold is not None and bandwidth <= squeeze_threshold:
                signals.append(
                    IndicatorSignal(
                        date=d,
                        kind="squeeze",
                        severity="info",
                        note="сжатие полос (bandwidth низкий) — ожидание движения",
                    )
                )
        prev_close = c

    last_middle = next((v.value for v in reversed(values) if v.kind == "middle"), None)
    last_upper = next((v.value for v in reversed(values) if v.kind == "upper"), None)
    last_lower = next((v.value for v in reversed(values) if v.kind == "lower"), None)
    last_close = closes[-1]

    return IndicatorResult(
        indicator="bollinger",
        params=p,
        values=values,
        signals=signals,
        meta={
            "period": period,
            "k": k,
            "latest_middle": round(last_middle, 4) if last_middle is not None else None,
            "latest_upper": round(last_upper, 4) if last_upper is not None else None,
            "latest_lower": round(last_lower, 4) if last_lower is not None else None,
            "last_close": round(last_close, 4),
            "percent_b": (
                round((last_close - last_lower) / (last_upper - last_lower), 4)
                if last_upper is not None and last_lower is not None and last_upper != last_lower
                else None
            ),
            "zone": (
                "upper"
                if last_upper is not None and last_close > last_upper
                else "lower"
                if last_lower is not None and last_close < last_lower
                else "middle"
                if last_lower is not None and last_upper is not None
                else "unknown"
            ),
            "candles": len(closes),
            "from": dates[0].isoformat(),
            "to": dates[-1].isoformat(),
        },
    )
