from datetime import date

from app.market.indicators.base import IndicatorResult, IndicatorSignal

DEFAULT_PARAMS = {
    "period": 60,
    "bins": 60,
    "value_area_pct": 70.0,
    "hvn_factor": 2.0,
    "lvn_factor": 0.5,
}


def _candle_date(candle):
    return getattr(candle, "date", None) or getattr(candle, "trading_date", None)


def calculate_volume_profile(
    candles: list,
    params: dict | None = None,
) -> IndicatorResult:
    """Профиль объёма: распределение объёма торгов по ценовым барам за период.

    Выходы (в одном расчёте):
    - POC (Point of Control) — бар с максимальным объёмом;
    - Value Area — диапазон вокруг POC с value_area_pct% объёма (VAH/VAL — границы);
    - HVN (High Volume Node) — бары с объёмом выше hvn_factor × средний;
    - LVN (Low Volume Node) — бары с объёмом ниже lvn_factor × средний.

    candles — список объектов с атрибутами date, high, low, volume
    (объём каждого дня распределяется равномерно по барам между low и high).
    Окно строится по фактическим датам (последние `period` свечей с объёмом).
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    period = int(p["period"])
    bins = int(p["bins"])
    value_area_pct = float(p["value_area_pct"])
    hvn_factor = float(p["hvn_factor"])
    lvn_factor = float(p["lvn_factor"])

    valid = [
        c
        for c in candles
        if c.high is not None and c.low is not None and c.volume is not None
    ]
    valid = valid[-period:] if period > 0 else valid

    empty = IndicatorResult(
        indicator="volume_profile",
        params=p,
        values=[],
        signals=[],
        meta={"note": "недостаточно данных для профиля объёма"},
    )
    if len(valid) < 5:
        return empty

    lo = min(c.low for c in valid)
    hi = max(c.high for c in valid)
    if not (hi > lo):
        return empty

    step = (hi - lo) / bins
    volumes = [0.0] * bins
    for c in valid:
        i_lo = max(0, min(bins - 1, int((c.low - lo) / step)))
        i_hi = max(0, min(bins - 1, int((c.high - lo) / step)))
        n = i_hi - i_lo + 1
        per_bar = float(c.volume) / n
        for i in range(i_lo, i_hi + 1):
            volumes[i] += per_bar

    poc_idx = max(range(bins), key=lambda i: volumes[i])
    total = sum(volumes)
    avg = total / bins

    left = right = poc_idx
    acc = volumes[poc_idx]
    target = total * value_area_pct / 100.0
    while acc < target and (left > 0 or right < bins - 1):
        lv = volumes[left - 1] if left > 0 else -1.0
        rv = volumes[right + 1] if right < bins - 1 else -1.0
        if lv >= rv:
            left -= 1
            acc += volumes[left]
        else:
            right += 1
            acc += volumes[right]

    val_price = lo + left * step
    vah_price = lo + (right + 1) * step
    poc_price = lo + (poc_idx + 0.5) * step

    nodes = []
    for i, v in enumerate(volumes):
        price = lo + (i + 0.5) * step
        nodes.append(
            {
                "price": round(price, 4),
                "volume": round(v, 2),
                "is_poc": i == poc_idx,
                "in_value_area": left <= i <= right,
                "is_hvn": v > hvn_factor * avg,
                "is_lvn": v < lvn_factor * avg,
            }
        )

    signals = [
        IndicatorSignal(
            date=_candle_date(valid[-1]),
            kind="poc",
            severity="warning",
            note=(
                f"POC: {poc_price:.2f} — контрольная точка, "
                f"максимальный объём за период"
            ),
        ),
        IndicatorSignal(
            date=_candle_date(valid[-1]),
            kind="value_area",
            severity="warning",
            note=(
                f"Value Area: {val_price:.2f}–{vah_price:.2f} "
                f"(≈{value_area_pct:.0f}% объёма) — зона «справедливой цены»"
            ),
        ),
        IndicatorSignal(
            date=_candle_date(valid[-1]),
            kind="hvn",
            severity="warning",
            note=(
                f"HVN: узлы высокого объёма (>{hvn_factor:.1f}× среднего) — "
                f"поддержка/сопротивление"
            ),
        ),
        IndicatorSignal(
            date=_candle_date(valid[-1]),
            kind="lvn",
            severity="warning",
            note=(
                f"LVN: узлы низкого объёма (<{lvn_factor:.1f}× среднего) — "
                f"быстрые проходы цены"
            ),
        ),
    ]

    return IndicatorResult(
        indicator="volume_profile",
        params=p,
        values=[],
        signals=signals,
        meta={
            "nodes": nodes,
            "poc": round(poc_price, 4),
            "vah": round(vah_price, 4),
            "val": round(val_price, 4),
            "value_area_pct": value_area_pct,
            "hvn_factor": hvn_factor,
            "lvn_factor": lvn_factor,
            "candles": len(valid),
            "from": _candle_date(valid[0]).isoformat(),
            "to": _candle_date(valid[-1]).isoformat(),
            "bins": bins,
        },
    )
