"""Базис «фьючерс против спота» (docs/19 §8.16).

Базис = цена фьючерса − цена спотового (базового) актива; показывает
контанго/бэквордацию и перекос фьючерсной цены относительно акции.

Формула: basis = F − S (рубли); basis_pct = (F − S) / S × 100.
Ряд строится по согласованным торговым датам спота и фьючерса.

Сигналы: contango (F > S), backwardation (F < S),
widening / narrowing (изменение |basis| за окно).
"""

from datetime import timedelta

from app.market.indicators.base import IndicatorResult, IndicatorSignal, IndicatorValue

DEFAULT_PARAMS = {
    "window": 5,  # окно для сигналов widening/narrowing
}


def calculate_basis(
    futures_prices: list[tuple],
    spot_prices: list[tuple],
    params: dict | None = None,
) -> IndicatorResult:
    """Базис по рядам (date, price) для фьючерса и спота.

    futures_prices / spot_prices — списки кортежей (date, price).
    Ряды согласуются по пересечению торговых дат (упорядочены по дате).
    """
    p = {**DEFAULT_PARAMS}
    for key, value in (params or {}).items():
        if value is not None:
            p[key] = value
    window = max(int(p["window"]), 1)

    f_map = {d: price for d, price in futures_prices}
    s_map = {d: price for d, price in spot_prices}
    common = sorted(set(f_map) & set(s_map))

    empty = IndicatorResult(
        indicator="basis",
        params=p,
        values=[],
        signals=[],
        meta={"note": "нет согласованных дат спота и фьючерса"},
    )
    if not common:
        return empty

    values: list[IndicatorValue] = []
    signals: list[IndicatorSignal] = []
    dates: list = []
    for d in common:
        basis = f_map[d] - s_map[d]
        val = round(basis, 4)
        dates.append(d)
        values.append(IndicatorValue(date=d, value=val, kind="basis"))
        if basis > 0:
            signals.append(
                IndicatorSignal(
                    date=d,
                    kind="contango",
                    severity="info",
                    note=f"контанго: фьючерс выше спота на {basis:.2f}",
                )
            )
        elif basis < 0:
            signals.append(
                IndicatorSignal(
                    date=d,
                    kind="backwardation",
                    severity="info",
                    note=f"бэквордация: фьючерс ниже спота на {abs(basis):.2f}",
                )
            )

    # widening/narrowing: |basis| меняется за окно (последние window значений)
    if len(values) >= 2:
        abs_series = [abs(v.value) for v in values]
        abs_win = abs_series[-(window + 1):]
        if len(abs_win) >= 2 and abs_win[-1] > abs_win[0]:
            signals.append(
                IndicatorSignal(
                    date=dates[-1],
                    kind="widening",
                    severity="warning",
                    note=(
                        f"|базис| растёт: с {abs_win[0]:.2f} до {abs_win[-1]:.2f} "
                        "за окно — перекос усиливается"
                    ),
                )
            )
        elif len(abs_win) >= 2 and abs_win[-1] < abs_win[0]:
            signals.append(
                IndicatorSignal(
                    date=dates[-1],
                    kind="narrowing",
                    severity="info",
                    note=(
                        f"|базис| сужается: с {abs_win[0]:.2f} до {abs_win[-1]:.2f} "
                        "за окно — перекос выравнивается"
                    ),
                )
            )

    last_basis = values[-1].value
    last_spot = s_map[dates[-1]]
    basis_pct = (last_basis / last_spot * 100.0) if last_spot else None

    return IndicatorResult(
        indicator="basis",
        params=p,
        values=values,
        signals=signals,
        meta={
            "window": window,
            "latest_basis": round(last_basis, 4),
            "latest_basis_pct": round(basis_pct, 4) if basis_pct is not None else None,
            "state": (
                "contango" if last_basis > 0
                else "backwardation" if last_basis < 0
                else "flat"
            ),
            "count": len(values),
            "from": dates[0].isoformat(),
            "to": dates[-1].isoformat(),
        },
    )
