"""ADX/DI — средний направленный индекс (docs/19 §8.10).

+DM = Hₜ − Hₜ₋₁ (если > 0 и > −DM), −DM = Lₜ₋₁ − Lₜ (аналогично);
сглаживание Уайлдера; +DI = 100·smooth(+DM)/TR_sum,
−DI = 100·smooth(−DM)/TR_sum; DX = 100·|+DI − −DI| / (+DI + −DI);
ADX = сглаженное DX (n = 14).

Сигналы: trend (ADX ≥ 25), range (ADX < 20), bullish / bearish (+DI × −DI).
"""

from app.market.indicators.base import IndicatorResult, IndicatorSignal, IndicatorValue

DEFAULT_PARAMS = {
    "period": 14,
}


def _candle_date(candle):
    return getattr(candle, "date", None) or getattr(candle, "trading_date", None)


def _smoothed(series: list[float], period: int) -> list[float]:
    """Сглаживание Уайлдера (экспоненциальное, аналог ATR-сглаживания)."""
    out: list[float] = []
    if not series:
        return out
    # seed: простое среднее первых period значений
    acc = sum(series[:period]) / period
    out.append(acc)
    for i in range(period, len(series)):
        acc = (acc * (period - 1) + series[i]) / period
        out.append(acc)
    return out


def calculate_adx(
    candles: list,
    params: dict | None = None,
) -> IndicatorResult:
    """ADX и ±DI по свечам (high/low/close).

    candles — список объектов с атрибутами date (или trading_date), high и low.
    """
    p = {**DEFAULT_PARAMS}
    for key, value in (params or {}).items():
        if value is not None:
            p[key] = value
    period = int(p["period"])
    if period <= 0:
        return IndicatorResult(
            indicator="adx",
            params=p,
            values=[],
            signals=[],
            meta={"note": "некорректные параметры"},
        )

    valid = [
        c
        for c in candles
        if getattr(c, "high", None) is not None and getattr(c, "low", None) is not None
    ]
    dates = [_candle_date(c) for c in valid]

    empty = IndicatorResult(
        indicator="adx",
        params=p,
        values=[],
        signals=[],
        meta={"note": "недостаточно данных для ADX (нужны high/low)"},
    )
    if len(valid) < 2 * period + 1:
        return empty

    plus_dm: list[float] = []
    minus_dm: list[float] = []
    tr_list: list[float] = []
    for i in range(1, len(valid)):
        h, l, ph, pl, pc = (
            valid[i].high,
            valid[i].low,
            valid[i - 1].high,
            valid[i - 1].low,
            valid[i - 1].close,
        )
        up_move = h - ph
        down_move = pl - l
        pdm = up_move if (up_move > 0 and up_move > down_move) else 0.0
        mdm = down_move if (down_move > 0 and down_move > up_move) else 0.0
        plus_dm.append(pdm)
        minus_dm.append(mdm)
        tr_list.append(max(h - l, abs(h - pc), abs(l - pc)))

    smooth_pdm = _smoothed(plus_dm, period)
    smooth_mdm = _smoothed(minus_dm, period)
    smooth_tr = _smoothed(tr_list, period)

    plus_di: list[float] = []
    minus_di: list[float] = []
    dx_list: list[float] = []
    for sp, sm, st in zip(smooth_pdm, smooth_mdm, smooth_tr):
        if st == 0:
            plus_di.append(0.0)
            minus_di.append(0.0)
            dx_list.append(0.0)
        else:
            pdi = 100.0 * sp / st
            mdi = 100.0 * sm / st
            plus_di.append(pdi)
            minus_di.append(mdi)
            denom = pdi + mdi
            dx_list.append(100.0 * abs(pdi - mdi) / denom if denom else 0.0)

    adx_list = _smoothed(dx_list, period)

    # Выравнивание по датам:
    #  - DI: первые +DI/−DI доступны со свечи под номером period (seed
    #    сглаживания Уайлдера покрывает TR/DM свечей 1..period);
    #  - ADX: вторая гладкая DX — ADX валиден только со свечи 2*period-1
    #    (DX накоплен за period значений).
    values: list[IndicatorValue] = []
    signals: list[IndicatorSignal] = []

    di_start = period
    prev_pdi = prev_mdi = None
    for j, (d, pdi, mdi) in enumerate(
        zip(dates[di_start:], plus_di, minus_di)
    ):
        values.append(IndicatorValue(date=d, value=round(pdi, 4), kind="plus_di"))
        values.append(IndicatorValue(date=d, value=round(mdi, 4), kind="minus_di"))
        if prev_pdi is not None and prev_mdi is not None:
            if prev_pdi <= prev_mdi and pdi > mdi:
                signals.append(
                    IndicatorSignal(
                        date=d,
                        kind="bullish",
                        severity="warning",
                        note="+DI выше −DI — бычий настрой тренда",
                    )
                )
            elif prev_pdi >= prev_mdi and pdi < mdi:
                signals.append(
                    IndicatorSignal(
                        date=d,
                        kind="bearish",
                        severity="warning",
                        note="−DI выше +DI — медвежий настрой тренда",
                    )
                )
        prev_pdi, prev_mdi = pdi, mdi

    adx_start = 2 * period - 1
    for j, adx in enumerate(adx_list):
        d = dates[adx_start + j]
        values.append(IndicatorValue(date=d, value=round(adx, 4), kind="adx"))
        if adx >= 25:
            signals.append(
                IndicatorSignal(
                    date=d,
                    kind="trend",
                    severity="info",
                    note=f"ADX={adx:.1f} — выраженный тренд",
                )
            )
        elif adx < 20:
            signals.append(
                IndicatorSignal(
                    date=d,
                    kind="range",
                    severity="info",
                    note=f"ADX={adx:.1f} — флэт (диапазон)",
                )
            )

    last_pdi = next((v.value for v in reversed(values) if v.kind == "plus_di"), None)
    last_mdi = next((v.value for v in reversed(values) if v.kind == "minus_di"), None)
    last_adx = next((v.value for v in reversed(values) if v.kind == "adx"), None)

    return IndicatorResult(
        indicator="adx",
        params=p,
        values=values,
        signals=signals,
        meta={
            "period": period,
            "latest_adx": round(last_adx, 4) if last_adx is not None else None,
            "latest_plus_di": round(last_pdi, 4) if last_pdi is not None else None,
            "latest_minus_di": round(last_mdi, 4) if last_mdi is not None else None,
            "trend": (
                "up" if last_pdi is not None and last_mdi is not None and last_pdi > last_mdi
                else "down"
                if last_pdi is not None and last_mdi is not None and last_pdi < last_mdi
                else "unknown"
            ),
            "state": (
                "trend"
                if last_adx is not None and last_adx >= 25
                else "range"
                if last_adx is not None and last_adx < 20
                else "unknown"
            ),
            "candles": len(valid),
            "from": dates[0].isoformat(),
            "to": dates[-1].isoformat(),
        },
    )
