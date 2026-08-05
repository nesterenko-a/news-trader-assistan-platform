from datetime import date

from app.market.indicators.base import IndicatorResult, IndicatorSignal, IndicatorValue

DEFAULT_PARAMS = {
    "oi_change_threshold_pct": 1.0,
    "price_change_threshold_pct": 0.0,
}

_SIGNAL_TEXT = {
    "strong_bull": "Цена растёт ↑, OI растёт ↑ — набор + длинных позиций",
    "strong_bear": "Цена падает ↓, OI растёт ↑ — открытие − коротких позиций",
    "long_liquidation": "Цена падает ↓, OI падает ↓ — закрытие длинных позиций",
    "short_covering": "Цена растёт ↑, OI падает ↓ — закрытие коротких позиций",
    "bearish_setup": "Цена без изменений →, OI растёт ↑ — рынок готовится к снижению",
    "bullish_setup": "Цена без изменений →, OI падает ↓ — рынок готовится к росту",
}


def calculate_oi(
    series: list[tuple[date, float | None, int | None, float | None]],
    params: dict | None = None,
) -> IndicatorResult:
    """Серия OI и сигналы «цена × OI × объём».

    series — отсортированный по датам список (date, close, open_position[, volume]).
    close/open_position/volume могут быть None (пропуски не участвуют в сигналах).
    Объём подтверждает/ослабляет сигнал: рост объёма к предыдущему дню — «up»,
    падение — «down» (пометка в сигнале и трактовке).
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    oi_threshold = float(p["oi_change_threshold_pct"])
    price_threshold = float(p["price_change_threshold_pct"])

    values: list[IndicatorValue] = []
    signals: list[IndicatorSignal] = []
    prev_close: float | None = None
    prev_oi: float | None = None
    prev_volume: float | None = None

    for row in series:
        row_date, close, oi = row[0], row[1], row[2]
        volume = row[3] if len(row) > 3 else None
        if oi is None:
            continue
        oi_value = float(oi)
        values.append(IndicatorValue(date=row_date, value=oi_value, kind="oi"))

        if prev_oi is not None and prev_oi != 0:
            oi_change_pct = (oi_value - prev_oi) / prev_oi * 100.0
            values.append(
                IndicatorValue(
                    date=row_date, value=round(oi_change_pct, 4), kind="oi_change_pct"
                )
            )
            volume_flag = None
            if (
                volume is not None
                and prev_volume is not None
                and prev_volume != 0
            ):
                volume_change_pct = (volume - prev_volume) / prev_volume * 100.0
                volume_flag = "up" if volume_change_pct > 0 else "down"
                values.append(
                    IndicatorValue(
                        date=row_date,
                        value=round(volume_change_pct, 4),
                        kind="volume_change_pct",
                    )
                )
            if close is not None and prev_close not in (None, 0):
                price_change_pct = (close - prev_close) / prev_close * 100.0
                signal = _classify(
                    price_change_pct, oi_change_pct, price_threshold, oi_threshold
                )
                if signal:
                    note = _SIGNAL_TEXT[signal]
                    if volume_flag == "up":
                        note += " · объём растёт ↑"
                    elif volume_flag == "down":
                        note += " · объём падает ↓"
                    signals.append(
                        IndicatorSignal(
                            date=row_date,
                            kind=signal,
                            severity=(
                                "strong"
                                if signal in ("strong_bull", "strong_bear")
                                else "warning"
                            ),
                            note=note,
                            volume=volume_flag,
                        )
                    )
        prev_oi = oi_value
        prev_close = close
        prev_volume = volume

    return IndicatorResult(
        indicator="oi",
        params=p,
        values=values,
        signals=signals,
        meta={
            "candles": len(series),
            "from": series[0][0].isoformat() if series else None,
            "to": series[-1][0].isoformat() if series else None,
            "note": "OI доступен только для фьючерсов (срочный рынок MOEX)",
        },
    )


def _classify(
    price_pct: float,
    oi_pct: float,
    price_threshold: float,
    oi_threshold: float,
) -> str | None:
    price_up = price_pct > price_threshold
    price_down = price_pct < -price_threshold
    if oi_pct >= oi_threshold:
        if price_up:
            return "strong_bull"
        if price_down:
            return "strong_bear"
        return "bearish_setup"
    if oi_pct <= -oi_threshold:
        if price_down:
            return "long_liquidation"
        if price_up:
            return "short_covering"
        return "bullish_setup"
    return None
