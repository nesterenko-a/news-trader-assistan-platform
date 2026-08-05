from datetime import date

from app.db.models import Security
from app.web.router import (
    _build_change_bars,
    _build_dual_chart,
    _build_volume_bars,
    _effective_sectors,
    _filter_securities,
)


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


def _securities():
    return [
        Security(id=1, ticker="AFLT", name="Аэрофлот", security_type="stock", sector="Авиаперевозки", market="MOEX"),
        Security(id=2, ticker="AFLT-6.26", name="AEROFLOT-6.26", security_type="futures", sector="", market="MOEX", assetcode="AFLT", lastdeldate=date(2026, 6, 18)),
        Security(id=3, ticker="SBER", name="Сбер", security_type="stock", sector="Банки", market="MOEX"),
    ]


def test_effective_sectors_futures_inherit_base_sector():
    effective = _effective_sectors(_securities())
    assert effective[2] == "Авиаперевозки"
    assert effective[3] == "Банки"


def test_filter_securities_by_type():
    securities = _securities()
    effective = _effective_sectors(securities)
    stocks = _filter_securities(securities, effective, "", "", "stocks")
    futures = _filter_securities(securities, effective, "", "", "futures")
    all_ = _filter_securities(securities, effective, "", "", "all")
    assert [s.ticker for s in stocks] == ["AFLT", "SBER"]
    assert [s.ticker for s in futures] == ["AFLT-6.26"]
    assert len(all_) == 3


def test_filter_securities_by_sector_uses_effective():
    securities = _securities()
    effective = _effective_sectors(securities)
    filtered = _filter_securities(securities, effective, "Авиаперевозки", "", "all")
    assert [s.ticker for s in filtered] == ["AFLT", "AFLT-6.26"]


def test_build_volume_bars():
    chart = _build_volume_bars(
        [(date(2026, 8, 1), 100.0), (date(2026, 8, 2), 200.0), (date(2026, 8, 3), None)]
    )
    assert chart is not None
    assert chart["rects"].count("<rect") == 2
    assert chart["max_volume"] == 200


def test_build_volume_bars_empty():
    assert _build_volume_bars([]) is None
