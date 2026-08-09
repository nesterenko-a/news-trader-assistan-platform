from datetime import date, timedelta

from app.market.indicators.support_resistance import (
    calculate_support_resistance,
)


class C:
    def __init__(self, d, o, h, l, c):
        self.date = d
        self.open = o
        self.high = h
        self.low = l
        self.close = c


def _series(prices, start=date(2026, 1, 1)):
    """prices: список (open, high, low, close)."""
    out = []
    for i, (o, h, l, c) in enumerate(prices):
        out.append(C(start + timedelta(days=i), o, h, l, c))
    return out


def test_atr_and_levels_in_range():
    # Цена колеблется между 100 и 110 (касания поддержки/сопротивления)
    prices = []
    for _ in range(40):
        prices.append((104, 110, 100, 105))
    candles = _series(prices)
    res = calculate_support_resistance(candles, params={"window": 30})
    assert res.meta.get("levels"), "должны быть уровни"
    levels = res.meta["levels"]
    kinds = {lv["kind"] for lv in levels}
    assert "support" in kinds and "resistance" in kinds
    # Уровни близки к 100 (поддержка) и 110 (сопротивление)
    sup = min(lv["price"] for lv in levels if lv["kind"] == "support")
    resi = min(lv["price"] for lv in levels if lv["kind"] == "resistance")
    assert abs(sup - 100) < 3, f"поддержка {sup}"
    assert abs(resi - 110) < 3, f"сопротивление {resi}"
    for lv in levels:
        assert lv["touches"] >= 2, "фильтр по касаниям"
        assert lv["strength"] in ("medium", "strong")


def test_breakout_up_signal():
    # Долгий боковик 100–110, затем свеча, начавшаяся ниже 110 и закрывшаяся выше
    prices = [(104, 110, 100, 105)] * 30
    prices.append((108, 118, 107, 117))  # пробой уровня 110 вверх
    candles = _series(prices)
    res = calculate_support_resistance(candles)
    kinds = {s.kind for s in res.signals}
    assert "breakout_up" in kinds, kinds


def test_bounce_up_signal():
    # Боковик 111–119 с вариациями, последняя свеча касается 100 и закрывается выше
    prices = [
        (115, 118, 112, 116), (114, 117, 111, 115), (115, 119, 113, 116),
        (114, 117, 112, 115), (115, 118, 111, 116), (114, 116, 112, 115),
        (115, 118, 112, 116), (114, 117, 111, 115), (115, 119, 113, 116),
        (114, 117, 112, 115), (115, 118, 111, 116), (114, 116, 112, 115),
        (115, 116, 100, 115),  # касание поддержки, закрытие выше
    ]
    candles = _series(prices)
    res = calculate_support_resistance(candles)
    kinds = {s.kind for s in res.signals}
    assert "bounce_up" in kinds, kinds


def test_insufficient_data():
    candles = _series([(100, 101, 99, 100)] * 5)
    res = calculate_support_resistance(candles)
    assert res.signals == []
    assert "недостаточно данных" in res.meta.get("note", "")
