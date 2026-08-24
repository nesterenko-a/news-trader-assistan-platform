"""Тесты индикатора ADX/DI (docs/19 §8.10)."""

from datetime import date, timedelta

from app.market.indicators.adx import DEFAULT_PARAMS, calculate_adx
from app.market.indicators.registry import REGISTRY


def _candles(prices: list[tuple[float, float, float]], start: date = date(2026, 1, 1)):
    class Candle:
        def __init__(self, trading_date, high, low, close):
            self.trading_date = trading_date
            self.high = high
            self.low = low
            self.close = close

    return [Candle(start + timedelta(days=i), *p) for i, p in enumerate(prices)]


def test_adx_known_direction():
    # Явный растущий тренд: +DI заметно выше −DI, ADX растёт
    candles = _candles(
        [(10.0 + i, 10.0 + i - 1, 10.0 + i - 0.5) for i in range(40)]
    )
    result = calculate_adx(candles, params={"period": 14})
    assert result.indicator == "adx"
    assert result.meta["trend"] == "up"
    assert result.meta["latest_plus_di"] is not None
    assert result.meta["latest_minus_di"] is not None
    assert result.meta["latest_plus_di"] > result.meta["latest_minus_di"]


def test_adx_down_trend():
    candles = _candles(
        [(100.0 - i, 99.0 - i, 99.5 - i) for i in range(40)]
    )
    result = calculate_adx(candles, params={"period": 14})
    assert result.meta["trend"] == "down"
    assert result.meta["latest_minus_di"] > result.meta["latest_plus_di"]


def test_adx_flat_is_range():
    # Флэт: маленькие движения, ADX низкий, state=range
    candles = _candles([(10.05, 9.95, 10.0)] * 40)
    result = calculate_adx(candles, params={"period": 14})
    assert result.meta["state"] == "range"
    kinds = {s.kind for s in result.signals}
    assert "range" in kinds


def test_adx_insufficient():
    result = calculate_adx(_candles([(10.0, 9.0, 9.5)] * 20), params={"period": 14})
    # data length 20 < 2*14+1 = 29
    assert result.values == []
    assert result.meta["note"]


def test_adx_params():
    assert DEFAULT_PARAMS == {"period": 14}


def test_adx_date_alignment():
    """DI валиден со свечи `period`, ADX — со свечи `2*period-1` (без утечки будущего)."""
    candles = _candles([(100.0 + 0.3 * i, 99.5 + 0.3 * i, 100.0 + 0.3 * i) for i in range(40)])
    result = calculate_adx(candles, params={"period": 14})
    first_di = next(v for v in result.values if v.kind == "plus_di")
    first_adx = next(v for v in result.values if v.kind == "adx")
    base = date(2026, 1, 1)
    assert first_di.date == base + timedelta(days=14)          # период
    assert first_adx.date == base + timedelta(days=2 * 14 - 1)  # 2*period-1


def test_adx_registry():
    assert "adx" in REGISTRY
    assert REGISTRY["adx"]["params"] == DEFAULT_PARAMS


def test_adx_missing_high_low():
    candles = _candles([(10.0, 9.0, 9.5)] * 40)
    candles[0].low = None
    result = calculate_adx(candles, params={"period": 14})
    assert result.meta["candles"] == 39
