"""Тесты индикатора ATR (docs/19 §8.8)."""

from datetime import date, timedelta

from app.market.indicators.atr import DEFAULT_PARAMS, calculate_atr
from app.market.indicators.registry import REGISTRY


def _candles(prices: list[tuple[float, float, float]], start: date = date(2026, 1, 1)):
    class Candle:
        def __init__(self, trading_date, high, low, close):
            self.trading_date = trading_date
            self.high = high
            self.low = low
            self.close = close

    return [Candle(start + timedelta(days=i), *p) for i, p in enumerate(prices)]


def test_atr_known_values():
    # Плоские свечи: range 10-8=2 каждый день, prev close равен prev low => TR=2.
    # period=2: seed = (2+2)/2 = 2; дальше ATRt = (ATR*1 + TR)/2 = 2.
    candles = _candles([(10.0, 8.0, 9.0)] * 6)
    result = calculate_atr(candles, params={"period": 2})
    assert result.indicator == "atr"
    assert result.signals == []
    atr_vals = [v.value for v in result.values]
    assert atr_vals == [2.0, 2.0, 2.0, 2.0]
    assert result.meta["latest_atr"] == 2.0
    assert result.meta["atr_pct"] == round(100.0 * 2.0 / 9.0, 4)
    # первое значение ATR — со свечи с индексом period (даты согласованы)
    assert result.values[0].date == date(2026, 1, 3)


def test_atr_wilder_smoothing():
    # Разные TR: проверим, что seed — среднее первых period TR.
    # TR за дни (i=1..): [0,2,4] -> period=2: seed=(0+2)/2=1; затем (1*1+4)/2=2.5
    candles = _candles(
        [
            (10.0, 10.0, 10.0),  # свеча 0 (не участвует в TR напрямую как prev)
            (10.0, 10.0, 10.0),  # TR=0
            (12.0, 10.0, 11.0),  # TR: max(2, |12-10|=2, |10-10|=0)=2
            (16.0, 12.0, 14.0),  # TR: max(4, |16-11|=5, |12-11|=1)=5
            (16.0, 14.0, 15.0),  # TR: max(2, |16-14|=2, |14-14|=0)=2
        ],
    )
    result = calculate_atr(candles, params={"period": 2})
    # dates: свечи 1..4 -> TR [0,2,5,2]; seed(2)=1 на свече idx2; затем 3.0 и 2.5
    vals = [v.value for v in result.values]
    assert vals == [1.0, 3.0, 2.5]


def test_atr_insufficient():
    result = calculate_atr(_candles([(10.0, 8.0, 9.0)] * 3), params={"period": 14})
    assert result.values == []
    assert result.meta["note"]


def test_atr_missing_high_low():
    # без close/высоких пропускаются; свечей с полными данными не хватает
    candles = _candles([(10.0, 8.0, 9.0)] * 20)
    for c in candles:
        if c.trading_date == date(2026, 1, 1):
            c.low = None
    result = calculate_atr(candles, params={"period": 3})
    assert result.meta["candles"] == 19


def test_atr_params():
    assert DEFAULT_PARAMS == {"period": 14}


def test_atr_registry():
    assert "atr" in REGISTRY
    assert REGISTRY["atr"]["params"] == DEFAULT_PARAMS
