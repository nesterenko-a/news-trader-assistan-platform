from datetime import date

from app.web.router import _build_change_bars, _build_dual_chart


def test_build_dual_chart_basic():
    series = [
        (date(2026, 8, 1), 1000.0, 100.0),
        (date(2026, 8, 2), 1100.0, 101.0),
        (date(2026, 8, 3), 1200.0, 102.0),
    ]
    chart = _build_dual_chart(series)
    assert chart is not None
    assert chart["oi_segments"] and chart["close_segments"]
    assert chart["min_oi"] < chart["max_oi"]
    assert chart["first_date"] == "2026-08-01"
    assert chart["last_date"] == "2026-08-03"


def test_build_dual_chart_gap_splits_segments():
    series = [
        (date(2026, 8, 1), 1000.0, 100.0),
        (date(2026, 8, 2), None, 101.0),
        (date(2026, 8, 3), 1200.0, 102.0),
    ]
    chart = _build_dual_chart(series)
    assert len(chart["oi_segments"]) == 2
    assert len(chart["close_segments"]) == 1


def test_build_dual_chart_all_none():
    assert _build_dual_chart([(date(2026, 8, 1), None, None)]) is None
    assert _build_dual_chart([]) is None


def test_build_change_bars():
    pairs = [
        (date(2026, 8, 1), 2.5),
        (date(2026, 8, 2), -1.0),
        (date(2026, 8, 3), None),
    ]
    chart = _build_change_bars(pairs)
    assert chart is not None
    assert chart["rects"].count("<rect") == 2
    assert chart["max_abs"] == 2.5


def test_build_change_bars_empty():
    assert _build_change_bars([]) is None
