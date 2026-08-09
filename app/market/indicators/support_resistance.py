from app.market.indicators.base import IndicatorResult, IndicatorSignal

DEFAULT_PARAMS = {
    "window": 20,
    "fractal_k": 2,
    "min_touches": 2,
    "cluster_tolerance_atr": 0.25,
    "atr_period": 14,
}


def _candle_date(candle):
    return getattr(candle, "date", None) or getattr(candle, "trading_date", None)


def _atr(candles: list, period: int) -> float | None:
    """ATR по Уайлдеру (период period)."""
    trs: list[float] = []
    prev_close = None
    for c in candles:
        if c.high is None or c.low is None:
            continue
        if prev_close is None:
            trs.append(c.high - c.low)
        else:
            trs.append(
                max(
                    c.high - c.low,
                    abs(c.high - prev_close),
                    abs(c.low - prev_close),
                )
            )
        prev_close = c.close
    if not trs:
        return None
    if len(trs) < period:
        return sum(trs) / len(trs)
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def _pivots(candle) -> dict[str, float]:
    """Классические pivot points от свечи."""
    high, low, close = candle.high, candle.low, candle.close
    p = (high + low + close) / 3.0
    return {
        "p": p,
        "r1": 2 * p - low,
        "s1": 2 * p - high,
        "r2": p + (high - low),
        "s2": p - (high - low),
    }


def _fractal_extremes(candles: list, k: int) -> list[tuple[int, float, str]]:
    """Фракталы Уильямса: (индекс, цена, kind) для локальных экстремумов."""
    out: list[tuple[int, float, str]] = []
    n = len(candles)
    for i in range(k, n - k):
        hi = candles[i].high
        lo = candles[i].low
        if all(candles[j].high < hi for j in range(i - k, i + k + 1) if j != i):
            out.append((i, hi, "resistance"))
        if all(candles[j].low > lo for j in range(i - k, i + k + 1) if j != i):
            out.append((i, lo, "support"))
    return out


def _touches(candles: list, price: float, tol: float) -> int:
    """Число свечей в окне, пересекающих уровень (low <= price <= high)."""
    return sum(1 for c in candles if c.low <= price <= c.high)


def _cluster_levels(
    candidates: list[tuple[float, int]], tol: float
) -> list[float]:
    """Объединяет близкие уровни (< tol); центр кластера — средневзвешенный
    по touches (вес касаний кандидата). Возвращает цены кластеров."""
    ordered = sorted(candidates)
    clusters: list[list[tuple[float, int]]] = []
    for price, touches in ordered:
        if clusters and price - clusters[-1][-1][0] < tol:
            clusters[-1].append((price, touches))
        else:
            clusters.append([(price, touches)])
    prices = []
    for cluster in clusters:
        total = sum(t for _, t in cluster)
        if total == 0:
            continue
        price = sum(p * t for p, t in cluster) / total
        prices.append(round(price, 4))
    return prices


def calculate_support_resistance(
    candles: list,
    params: dict | None = None,
) -> IndicatorResult:
    """Уровни поддержки/сопротивления (ТЗ docs/19 §8.11).

    Алгоритм: pivot points от последней свечи + локальные экстремумы
    (фракталы Уильямса) + кластеризация близких уровней (по ATR).
    Сила уровня — по числу касаний. Сигналы: bounce (отскок от уровня)
    и breakout (пробой уровня с подтверждением закрытием).

    candles — список объектов с атрибутами date, open, high, low, close.
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    window = int(p["window"])
    fractal_k = int(p["fractal_k"])
    min_touches = int(p["min_touches"])
    cluster_tolerance_atr = float(p["cluster_tolerance_atr"])
    atr_period = int(p["atr_period"])

    valid = [
        c
        for c in candles
        if c.high is not None and c.low is not None and c.close is not None
    ]
    valid = valid[-window:] if window > 0 else valid

    empty = IndicatorResult(
        indicator="support_resistance",
        params=p,
        values=[],
        signals=[],
        meta={"note": "недостаточно данных для уровней"},
    )
    if len(valid) < max(fractal_k * 2 + 3, 10):
        return empty

    atr = _atr(valid, atr_period)
    if atr is None or atr <= 0:
        return empty

    tol = cluster_tolerance_atr * atr
    last = valid[-1]
    last_close = last.close

    # Кандидаты: pivot points + фракталы (цена, touches)
    piv = _pivots(last)
    candidates: list[tuple[float, int]] = []
    for key in ("r1", "r2", "s1", "s2"):
        price = piv[key]
        candidates.append((price, _touches(valid, price, tol)))
    for _idx, price, _kind in _fractal_extremes(valid, fractal_k):
        candidates.append((price, _touches(valid, price, tol)))

    # Кластеризация и фильтр по фактическим касаниям (пересчёт по свечам)
    levels = [
        {
            **lv,
            "touches": _touches(valid, lv["price"], tol),
            "strength": "strong"
            if _touches(valid, lv["price"], tol) >= 4
            else "medium",
        }
        for lv in (
            {"price": price}
            for price in _cluster_levels(candidates, tol)
        )
        if _touches(valid, lv["price"], tol) >= min_touches
    ]
    for lv in levels:
        lv["kind"] = (
            "resistance" if lv["price"] >= last_close else "support"
        )
    levels.sort(key=lambda lv: abs(lv["price"] - last_close))

    # Сигналы по последней свече
    signals: list[IndicatorSignal] = []
    if levels:
        nearest_res = next(
            (lv for lv in levels if lv["kind"] == "resistance"), None
        )
        nearest_sup = next(
            (lv for lv in levels if lv["kind"] == "support"), None
        )
        d = _candle_date(last)
        # Пробой: свеча началась по одну сторону уровня и закрылась по другую
        below = [lv for lv in levels if lv["price"] < last_close - tol]
        above = [lv for lv in levels if lv["price"] > last_close + tol]
        crossed_up = (
            max(below, key=lambda lv: lv["price"])
            if below
            else None
        )
        crossed_down = (
            min(above, key=lambda lv: lv["price"])
            if above
            else None
        )
        if crossed_up and last.open <= crossed_up["price"]:
            signals.append(
                IndicatorSignal(
                    date=d,
                    kind="breakout_up",
                    severity="warning",
                    note=(
                        f"Пробой уровня {crossed_up['price']:.2f} вверх — "
                        f"подтверждён закрытием {last_close:.2f}"
                    ),
                )
            )
        elif crossed_down and last.open >= crossed_down["price"]:
            signals.append(
                IndicatorSignal(
                    date=d,
                    kind="breakout_down",
                    severity="warning",
                    note=(
                        f"Пробой уровня {crossed_down['price']:.2f} вниз — "
                        f"подтверждён закрытием {last_close:.2f}"
                    ),
                )
            )
        elif nearest_res and last.high >= nearest_res["price"] - tol and (
            last_close < nearest_res["price"] - tol
        ):
            signals.append(
                IndicatorSignal(
                    date=d,
                    kind="bounce_down",
                    severity="warning",
                    note=(
                        f"Отскок от сопротивления {nearest_res['price']:.2f} "
                        f"— цена коснулась и закрылась ниже"
                    ),
                )
            )
        elif nearest_sup and last.low <= nearest_sup["price"] + tol and (
            last_close > nearest_sup["price"] + tol
        ):
            signals.append(
                IndicatorSignal(
                    date=d,
                    kind="bounce_up",
                    severity="warning",
                    note=(
                        f"Отскок от поддержки {nearest_sup['price']:.2f} — "
                        f"цена коснулась и закрылась выше"
                    ),
                )
            )

    return IndicatorResult(
        indicator="support_resistance",
        params=p,
        values=[],
        signals=signals,
        meta={
            "levels": levels,
            "pivot": {k: round(v, 4) for k, v in piv.items()},
            "atr": round(atr, 4),
            "last_close": round(last_close, 4),
            "candles": len(valid),
            "from": _candle_date(valid[0]).isoformat(),
            "to": _candle_date(last).isoformat(),
            "window": window,
        },
    )
