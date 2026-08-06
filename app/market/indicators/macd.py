"""MACD — схождение/расхождение скользящих средних (docs/19 §8.2).

MACD = EMA(fast) − EMA(slow); signal = EMA(signal) от MACD; hist = MACD − signal.
Сигналы: cross_up / cross_down (MACD × signal), hist_positive / hist_negative.
"""

from app.market.indicators.base import IndicatorResult, IndicatorSignal, IndicatorValue
from app.market.indicators.ema import _ema

DEFAULT_PARAMS = {
    "fast": 12,
    "slow": 26,
    "signal": 9,
}


def _candle_date(candle):
    return getattr(candle, "date", None) or getattr(candle, "trading_date", None)


def calculate_macd(
    candles: list,
    params: dict | None = None,
) -> IndicatorResult:
    """MACD по свечам: values (macd/signal/hist) и сигналы пересечения и знака.

    candles — список объектов с атрибутами date (или trading_date) и close.
    """
    p = {**DEFAULT_PARAMS}
    for key, value in (params or {}).items():
        if value is not None:
            p[key] = value
    fast = int(p["fast"])
    slow = int(p["slow"])
    signal = int(p["signal"])

    valid = [(c, c.close) for c in candles if getattr(c, "close", None) is not None]
    dates = [_candle_date(c) for c, _ in valid]
    closes = [close for _, close in valid]

    fast_series = _ema(closes, fast)
    slow_series = _ema(closes, slow)

    macd_series: list[float | None] = [
        (f if f is not None else 0.0) - (s if s is not None else 0.0)
        if f is not None and s is not None
        else None
        for f, s in zip(fast_series, slow_series)
    ]
    macd_values: list[float] = [m for m in macd_series if m is not None]
    if len(macd_values) < signal + 1:
        return IndicatorResult(
            indicator="macd",
            params=p,
            values=[],
            signals=[],
            meta={"note": "недостаточно данных для MACD"},
        )

    signal_ema = _ema(macd_values, signal)
    # Выравнивание: signal_ema короче macd_series на (fast-1 или slow-1, см. выше)
    # macd_series имеет None в начале (до seed более короткой EMA).
    first_macd = macd_series.index(next(m for m in macd_series if m is not None))
    signal_series: list[float | None] = [None] * first_macd + signal_ema
    signal_series = signal_series[: len(macd_series)]
    hist_series: list[float | None] = [
        (m - s) if m is not None and s is not None else None
        for m, s in zip(macd_series, signal_series)
    ]

    values: list[IndicatorValue] = []
    signals: list[IndicatorSignal] = []
    prev_macd = prev_signal = None
    prev_hist = None
    for i, d in enumerate(dates):
        m, s, h = macd_series[i], signal_series[i], hist_series[i]
        if m is not None:
            values.append(IndicatorValue(date=d, value=round(m, 4), kind="macd"))
        if s is not None:
            values.append(IndicatorValue(date=d, value=round(s, 4), kind="signal"))
        if h is not None:
            values.append(IndicatorValue(date=d, value=round(h, 4), kind="hist"))
        if (
            m is not None
            and s is not None
            and prev_macd is not None
            and prev_signal is not None
        ):
            if prev_macd <= prev_signal and m > s:
                signals.append(
                    IndicatorSignal(
                        date=d,
                        kind="cross_up",
                        severity="strong",
                        note="MACD пересекла signal снизу вверх — бычий сигнал",
                    )
                )
            elif prev_macd >= prev_signal and m < s:
                signals.append(
                    IndicatorSignal(
                        date=d,
                        kind="cross_down",
                        severity="strong",
                        note="MACD пересекла signal сверху вниз — медвежий сигнал",
                    )
                )
        if h is not None and prev_hist is not None:
            if prev_hist <= 0 < h:
                signals.append(
                    IndicatorSignal(
                        date=d,
                        kind="hist_positive",
                        severity="warning",
                        note="Гистограмма MACD стала положительной — импульс вверх",
                    )
                )
            elif prev_hist >= 0 > h:
                signals.append(
                    IndicatorSignal(
                        date=d,
                        kind="hist_negative",
                        severity="warning",
                        note="Гистограмма MACD стала отрицательной — импульс вниз",
                    )
                )
        prev_macd, prev_signal, prev_hist = m, s, h

    last_macd = next((v.value for v in reversed(values) if v.kind == "macd"), None)
    last_signal = next((v.value for v in reversed(values) if v.kind == "signal"), None)
    last_hist = next((v.value for v in reversed(values) if v.kind == "hist"), None)

    return IndicatorResult(
        indicator="macd",
        params=p,
        values=values,
        signals=signals,
        meta={
            "fast": fast,
            "slow": slow,
            "signal": signal,
            "latest_macd": round(last_macd, 4) if last_macd is not None else None,
            "latest_signal": round(last_signal, 4) if last_signal is not None else None,
            "latest_hist": round(last_hist, 4) if last_hist is not None else None,
            "trend": (
                "up"
                if last_macd is not None and last_signal is not None and last_macd > last_signal
                else "down"
                if last_macd is not None and last_signal is not None and last_macd < last_signal
                else "unknown"
            ),
            "candles": len(closes),
            "from": dates[0].isoformat(),
            "to": dates[-1].isoformat(),
        },
    )
