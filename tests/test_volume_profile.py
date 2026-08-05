from datetime import date, timedelta

from app.market.indicators.volume_profile import calculate_volume_profile


class Candle:
    def __init__(self, d, high, low, close, volume):
        self.date = d
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume


def _candles(n: int = 30):
    base = date(2026, 1, 1)
    return [
        Candle(base + timedelta(days=i), 100 + i, 90 + i, 95 + i, 1000)
        for i in range(n)
    ]


def test_poc_is_max_node():
    res = calculate_volume_profile(_candles())
    nodes = res.meta["nodes"]
    poc_node = max(nodes, key=lambda n: n["volume"])
    assert poc_node["is_poc"]
    assert res.meta["poc"] == poc_node["price"]


def test_value_area_contains_about_70_percent():
    res = calculate_volume_profile(_candles())
    nodes = res.meta["nodes"]
    va = sum(n["volume"] for n in nodes if n["in_value_area"])
    total = sum(n["volume"] for n in nodes)
    ratio = va / total
    assert 0.69 <= ratio <= 0.9
    assert res.meta["vah"] >= res.meta["val"]


def test_hvn_lvn_flags_by_factors():
    res = calculate_volume_profile(_candles())
    nodes = res.meta["nodes"]
    avg = sum(n["volume"] for n in nodes) / len(nodes)
    for n in nodes:
        if n["is_hvn"]:
            assert n["volume"] > 2.0 * avg
        if n["is_lvn"]:
            assert n["volume"] < 0.5 * avg


def test_insufficient_data():
    res = calculate_volume_profile([Candle(date(2026, 1, 1), 100, 90, 95, 1000)])
    assert res.signals == []
    assert "недостаточно" in res.meta["note"]


def test_period_param_limits_window():
    res = calculate_volume_profile(_candles(60), params={"period": 30})
    assert res.meta["candles"] == 30
