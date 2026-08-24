"""Тесты индикатора RSI (docs/19 §8.15)."""

from datetime import date, timedelta

from app.market.indicators.rsi_indicator import DEFAULT_PARAMS, calculate_rsi
from app.market.indicators.registry import REGISTRY


def _candles(prices: list[float], start: date = date(2026, 1, 1)):
    class Candle:
        def __init__(self, trading_date, close):
            self.trading_date = trading_date
            self.close = close

    return [Candle(start + timedelta(days=i), p) for i, p in enumerate(prices)]


def test_rsi_known_up_trend():
    # Монотонный рост: RSI ≈ 100 (потери = 0)
    candles = _candles([10.0 + i for i in range(40)])
    result = calculate_rsi(candles, params={"period": 14})
    assert result.indicator == "rsi"
    assert result.meta["latest_rsi"] == 100.0
    assert result.meta["state"] == "overbought"
    # сигнал overbought есть
    assert any(s.kind == "overbought" for s in result.signals)


def test_rsi_known_down_trend():
    # Монотонное падение: RSI ≈ 0
    candles = _candles([100.0 - i for i in range(40)])
    result = calculate_rsi(candles, params={"period": 14})
    assert result.meta["latest_rsi"] == 0.0
    assert result.meta["state"] == "oversold"
    assert any(s.kind == "oversold" for s in result.signals)


def test_rsi_not_enough_data():
    candles = _candles([1.0, 2.0, 3.0])
    result = calculate_rsi(candles, params={"period": 14})
    assert result.values == []
    assert result.meta.get("note", "").startswith("недостаточно данных")


def test_rsi_in_registry():
    assert "rsi" in REGISTRY
    entry = REGISTRY["rsi"]
    assert "stocks" in entry["markets"]
    assert "futures" in entry["markets"]
    assert entry["params"] == DEFAULT_PARAMS


def test_rsi_series_value_range():
    # Пилообразные данные: RSI строго между 0 и 100, не NaN
    prices = [100.0 + (10.0 if i % 2 == 0 else -5.0) for i in range(60)]
    candles = _candles(prices)
    result = calculate_rsi(candles, params={"period": 14})
    assert result.values
    for v in result.values:
        assert 0.0 <= v.value <= 100.0
    assert result.meta["latest_rsi"] is not None
