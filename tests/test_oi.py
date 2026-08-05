from datetime import date, timedelta

from app.market.indicators.oi import calculate_oi


def _series(closes: list[float], ois: list[int]) -> list[tuple[date, float | None, int | None]]:
    d0 = date(2026, 8, 1)
    return [
        (d0 + timedelta(days=i), c, o) for i, (c, o) in enumerate(zip(closes, ois))
    ]


def test_strong_bull():
    res = calculate_oi(_series([100, 101, 102], [1000, 1100, 1200]))
    kinds = [s.kind for s in res.signals]
    assert "strong_bull" in kinds
    assert all(s.severity == "strong" for s in res.signals if s.kind == "strong_bull")


def test_strong_bear():
    res = calculate_oi(_series([104, 103, 102], [1000, 1100, 1200]))
    assert "strong_bear" in [s.kind for s in res.signals]


def test_long_liquidation():
    res = calculate_oi(_series([104, 103, 102], [1200, 1100, 1000]))
    assert "long_liquidation" in [s.kind for s in res.signals]
    assert res.signals[0].severity == "warning"


def test_short_covering():
    res = calculate_oi(_series([100, 101, 102], [1200, 1100, 1000]))
    assert "short_covering" in [s.kind for s in res.signals]


def test_volume_up_confirms_signal():
    d0 = date(2026, 8, 1)
    series = [
        (d0 + timedelta(days=i), c, o, v)
        for i, (c, o, v) in enumerate(
            zip([100, 101, 102], [1000, 1100, 1200], [100, 200, 300])
        )
    ]
    res = calculate_oi(series)
    bull = [s for s in res.signals if s.kind == "strong_bull"]
    assert bull
    assert all(s.volume == "up" for s in bull)
    assert "объём растёт ↑" in bull[0].note


def test_volume_down_weakens_signal():
    d0 = date(2026, 8, 1)
    series = [
        (d0 + timedelta(days=i), c, o, v)
        for i, (c, o, v) in enumerate(
            zip([100, 101, 102], [1000, 1100, 1200], [300, 200, 100])
        )
    ]
    res = calculate_oi(series)
    bull = [s for s in res.signals if s.kind == "strong_bull"]
    assert bull
    assert all(s.volume == "down" for s in bull)
    assert "объём падает ↓" in bull[0].note


def test_no_volume_no_flag():
    res = calculate_oi(_series([100, 101, 102], [1000, 1100, 1200]))
    bull = [s for s in res.signals if s.kind == "strong_bull"]
    assert bull
    assert all(s.volume is None for s in bull)


def test_bearish_setup_price_flat_oi_up():
    res = calculate_oi(_series([100, 100, 100], [1000, 1100, 1200]))
    signals = [s for s in res.signals if s.kind == "bearish_setup"]
    assert signals
    assert all(s.severity == "warning" for s in signals)
    assert "→" in signals[0].note


def test_bullish_setup_price_flat_oi_down():
    res = calculate_oi(_series([100, 100, 100], [1200, 1100, 1000]))
    signals = [s for s in res.signals if s.kind == "bullish_setup"]
    assert signals
    assert all(s.severity == "warning" for s in signals)
    assert "→" in signals[0].note


def test_notes_contain_arrows():
    res = calculate_oi(_series([100, 101, 102], [1000, 1100, 1200]))
    note = next(s.note for s in res.signals if s.kind == "strong_bull")
    assert "↑" in note
    assert "+" in note


def test_no_signal_below_threshold():
    res = calculate_oi(_series([100, 101, 102], [1000, 1005, 1010]))
    assert res.signals == []


def test_custom_threshold():
    res = calculate_oi(
        _series([100, 101, 102], [1000, 1005, 1010]),
        params={"oi_change_threshold_pct": 0.4},
    )
    assert "strong_bull" in [s.kind for s in res.signals]


def test_missing_oi_day_skipped():
    d0 = date(2026, 8, 1)
    series = [
        (d0, 100.0, 1000),
        (d0 + timedelta(days=1), 101.0, None),
        (d0 + timedelta(days=2), 102.0, 1100),
    ]
    res = calculate_oi(series)
    assert len([v for v in res.values if v.kind == "oi"]) == 2
    assert res.signals


def test_values_and_meta():
    res = calculate_oi(_series([100, 101], [1000, 1100]))
    assert res.indicator == "oi"
    kinds = {v.kind for v in res.values}
    assert kinds == {"oi", "oi_change_pct"}
    assert res.meta["candles"] == 2
    assert res.params["oi_change_threshold_pct"] == 1.0
