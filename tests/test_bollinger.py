"""Тесты индикатора Полосы Боллинджера (docs/19 §8.3)."""

from datetime import date, timedelta

from app.market.indicators.bollinger import (
    DEFAULT_PARAMS,
    _rolling_std,
    _sma,
    calculate_bollinger,
)
from app.market.indicators.registry import REGISTRY


def _candles_hlc(prices: list[tuple[float, float, float]], start: date = date(2026, 1, 1)):
    class Candle:
        def __init__(self, trading_date, high, low, close):
            self.trading_date = trading_date
            self.high = high
            self.low = low
            self.close = close

    return [Candle(start + timedelta(days=i), *p) for i, p in enumerate(prices)]


def _candles_close(closes: list[float], start: date = date(2026, 1, 1)):
    return _candles_hlc([(c, c, c) for c in closes], start)


# ---------- вспомогательные ----------

def test_sma():
    assert _sma([1.0, 2.0, 3.0, 4.0], 2) == [None, 1.5, 2.5, 3.5]


def test_sma_insufficient():
    assert _sma([1.0, 2.0], 5) == [None, None]


def test_rolling_std_population():
    # значения [2,4,4,4,5,5,7,9] — пример с Википедии по std; проверим окно 4
    vals = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
    std = _rolling_std(vals, 4)
    assert std[:3] == [None, None, None]
    # окно [2,4,4,4]: mean=3.5, var=0.75, std≈0.866
    assert std[3] is not None and abs(std[3] - 0.8660) < 1e-3


# ---------- calculate_bollinger ----------

def test_bollinger_known_values():
    # Константный ряд: средняя=полосам, даёт middle, зона middle, нет сигналов
    result = calculate_bollinger(
        _candles_close([10.0] * 30), params={"period": 5, "k": 2}
    )
    assert result.indicator == "bollinger"
    kinds = {v.kind for v in result.values}
    assert kinds == {"middle", "upper", "lower"}
    assert result.meta["zone"] == "middle"
    assert result.meta["latest_middle"] == 10.0
    assert result.meta["latest_upper"] == 10.0
    assert result.meta["latest_lower"] == 10.0
    assert result.meta["percent_b"] is None
    assert not result.signals


def test_bollinger_insufficient():
    result = calculate_bollinger(_candles_close([10.0, 11.0, 12.0]))
    assert result.values == []
    assert result.meta["note"]


def test_bollinger_touch_upper():
    # Мысленно: ряд, где цена резко уходит вверх за верхнюю полосу
    closes = [10.0] * 20 + [11.0, 12.5, 14.0, 16.0]
    result = calculate_bollinger(_candles_close(closes), params={"period": 20, "k": 2})
    kinds = {s.kind for s in result.signals}
    assert "touch_upper" in kinds
    assert result.meta["zone"] == "upper"


def test_bollinger_touch_lower():
    closes = [10.0] * 20 + [9.0, 7.5, 6.0, 4.0]
    result = calculate_bollinger(_candles_close(closes), params={"period": 20, "k": 2})
    kinds = {s.kind for s in result.signals}
    assert "touch_lower" in kinds
    assert result.meta["zone"] == "lower"


def test_bollinger_params():
    assert DEFAULT_PARAMS == {"period": 20, "k": 2}
    # k влияет на ширину полос
    narrow = calculate_bollinger(_candles_close([10.0, 11.0, 12.0] * 10), params={"k": 1})
    wide = calculate_bollinger(_candles_close([10.0, 11.0, 12.0] * 10), params={"k": 3})
    assert narrow.meta["latest_upper"] < wide.meta["latest_upper"]


def test_bollinger_invalid_params():
    result = calculate_bollinger(_candles_close([10.0] * 30), params={"period": 0})
    assert result.meta["note"]


def test_bollinger_registry():
    assert "bollinger" in REGISTRY
    assert REGISTRY["bollinger"]["params"] == DEFAULT_PARAMS


def test_bollinger_skips_none_closes():
    # свечи без close пропускаются
    candles = _candles_close([10.0] * 25)
    for c in candles:
        if c.trading_date == date(2026, 1, 5):
            c.close = None
    result = calculate_bollinger(candles, params={"period": 5})
    assert result.meta["candles"] == 24
