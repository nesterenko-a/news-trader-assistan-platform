"""RSI — индекс относительной силы (docs/19 §8.15).

RSI = 100 − 100/(1 + RS), RS = средняя_прибыль / средний_убыток за окно;
сглаживание Уайлдера (α = 1/period). Серия значений RSI по закрытиям свечей,
последнее значение — для запроса LLM и сигналов.

Сигналы: overbought (RSI ≥ 70), oversold (RSI ≤ 30),
cross_up/cross_down (пересечение 50 или выход из зоны 30/70),
revert (возврат из зоны).
"""

from app.market.indicators.base import IndicatorResult, IndicatorSignal, IndicatorValue

DEFAULT_PARAMS = {
    "period": 14,
}


def _candle_date(candle):
    return getattr(candle, "date", None) or getattr(candle, "trading_date", None)


def rsi_series(closes: list[float], period: int = 14) -> list[float]:
    """Серия значений RSI по закрытиям (Уайлдеровское сглаживание).

    Возвращает список длиной len(closes) − period (первое значение — на
    свече под номером period+1), каждый элемент — RSI в [0..100].
    Если данных мало — возвращается пустой список.
    """
    if period <= 0 or len(closes) < period + 1:
        return []

    # Первичное простое среднее приростов/убытков за первые period переходов
    gains = 0.0
    losses = 0.0
    for i in range(period):
        change = closes[i + 1] - closes[i]
        if change >= 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / period
    avg_loss = losses / period

    out: list[float] = []
    for i in range(period + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        gain = change if change >= 0 else 0.0
        loss = -change if change < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            out.append(100.0)
        else:
            rs = avg_gain / avg_loss
            out.append(100.0 - (100.0 / (1.0 + rs)))
    return out


def _zone(value: float | None) -> str | None:
    if value is None:
        return None
    if value >= 70:
        return "overbought"
    if value <= 30:
        return "oversold"
    return None


def calculate_rsi(
    candles: list,
    params: dict | None = None,
) -> IndicatorResult:
    """RSI по свечам (закрытия).

    candles — список объектов с атрибутами date (или trading_date) и close.
    """
    p = {**DEFAULT_PARAMS}
    for key, value in (params or {}).items():
        if value is not None:
            p[key] = value
    period = int(p["period"])
    if period <= 0:
        return IndicatorResult(
            indicator="rsi",
            params=p,
            values=[],
            signals=[],
            meta={"note": "некорректные параметры"},
        )

    valid = [
        c
        for c in candles
        if getattr(c, "close", None) is not None
    ]
    dates = [_candle_date(c) for c in valid]
    closes = [c.close for c in valid]

    empty = IndicatorResult(
        indicator="rsi",
        params=p,
        values=[],
        signals=[],
        meta={"note": "недостаточно данных для RSI"},
    )
    if len(closes) < period + 1:
        return empty

    series = rsi_series(closes, period)
    if not series:
        return empty

    # Первое значение RSI соответствует свече под номером period+1 (0-based),
    # потому что первичное среднее считается по period переходам,
    # заканчивающимся на свече с индексом period.
    rsi_start = period + 1
    values: list[IndicatorValue] = []
    signals: list[IndicatorSignal] = []

    prev_rsi = None
    prev_zone = None
    for j, d in enumerate(dates[rsi_start:]):
        val = round(series[j], 4)
        values.append(IndicatorValue(date=d, value=val, kind="rsi"))
        zone = _zone(val)
        if prev_rsi is not None:
            # пересечение середины 50
            if prev_rsi <= 50 < val:
                signals.append(
                    IndicatorSignal(
                        date=d,
                        kind="cross_up",
                        severity="warning",
                        note="RSI пересекает 50 снизу вверх — усиление бычьего импульса",
                    )
                )
            elif prev_rsi >= 50 > val:
                signals.append(
                    IndicatorSignal(
                        date=d,
                        kind="cross_down",
                        severity="warning",
                        note="RSI пересекает 50 сверху вниз — ослабление бычьего импульса",
                    )
                )
            # выход из зоны перекупленности/перепроданности
            if prev_zone == "overbought" and zone != "overbought":
                signals.append(
                    IndicatorSignal(
                        date=d,
                        kind="revert",
                        severity="warning",
                        note="RSI покидает зону перекупленности — риск разворота вниз",
                    )
                )
            elif prev_zone == "oversold" and zone != "oversold":
                signals.append(
                    IndicatorSignal(
                        date=d,
                        kind="revert",
                        severity="warning",
                        note="RSI покидает зону перепроданности — риск разворота вверх",
                    )
                )
        if zone == "overbought":
            signals.append(
                IndicatorSignal(
                    date=d,
                    kind="overbought",
                    severity="warning",
                    note=f"RSI={val:.0f} — зона перекупленности",
                )
            )
        elif zone == "oversold":
            signals.append(
                IndicatorSignal(
                    date=d,
                    kind="oversold",
                    severity="warning",
                    note=f"RSI={val:.0f} — зона перепроданности",
                )
            )
        prev_rsi = val
        prev_zone = zone

    last_rsi = next((v.value for v in reversed(values) if v.kind == "rsi"), None)

    return IndicatorResult(
        indicator="rsi",
        params=p,
        values=values,
        signals=signals,
        meta={
            "period": period,
            "latest_rsi": round(last_rsi, 4) if last_rsi is not None else None,
            "state": _zone(last_rsi) or "neutral",
            "last_cross": ("overbought" if last_rsi is not None and last_rsi >= 70
                           else "oversold" if last_rsi is not None and last_rsi <= 30
                           else "neutral"),
            "candles": len(valid),
            "from": dates[0].isoformat(),
            "to": dates[-1].isoformat(),
        },
    )
