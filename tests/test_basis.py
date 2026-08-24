"""Тесты индикатора «Базис фьючерс против спота» (docs/19 §8.16)."""

from datetime import date, timedelta

from app.market.indicators.basis import DEFAULT_PARAMS, calculate_basis
from app.market.indicators.registry import REGISTRY


def _prices(start: date, closes: list[float]):
    return [(start + timedelta(days=i), p) for i, p in enumerate(closes)]


def test_basis_contango():
    # Фьючерс постоянно выше спота => контанго
    spot = _prices(date(2026, 1, 1), [100.0, 101.0, 102.0, 103.0, 104.0])
    fut = _prices(date(2026, 1, 1), [105.0, 106.0, 107.0, 108.0, 109.0])
    result = calculate_basis(fut, spot, params={"window": 3})
    assert result.indicator == "basis"
    assert result.meta["state"] == "contango"
    assert result.meta["latest_basis"] == 5.0
    # последний спот = 104.0 => 5/104*100
    assert result.meta["latest_basis_pct"] == round(5.0 / 104.0 * 100.0, 4)
    assert any(s.kind == "contango" for s in result.signals)


def test_basis_backwardation():
    spot = _prices(date(2026, 1, 1), [100.0, 101.0, 102.0, 103.0, 104.0])
    fut = _prices(date(2026, 1, 1), [95.0, 96.0, 97.0, 98.0, 99.0])
    result = calculate_basis(fut, spot, params={"window": 3})
    assert result.meta["state"] == "backwardation"
    assert result.meta["latest_basis"] == -5.0
    assert any(s.kind == "backwardation" for s in result.signals)


def test_basis_widening():
    # |базис| растёт => widening
    spot = _prices(date(2026, 1, 1), [100.0, 100.0, 100.0, 100.0, 100.0])
    fut = _prices(date(2026, 1, 1), [101.0, 102.0, 104.0, 107.0, 110.0])
    result = calculate_basis(fut, spot, params={"window": 3})
    assert any(s.kind == "widening" for s in result.signals)


def test_basis_narrowing():
    spot = _prices(date(2026, 1, 1), [100.0, 100.0, 100.0, 100.0, 100.0])
    fut = _prices(date(2026, 1, 1), [110.0, 107.0, 104.0, 102.0, 101.0])
    result = calculate_basis(fut, spot, params={"window": 3})
    assert any(s.kind == "narrowing" for s in result.signals)


def test_basis_no_common_dates():
    spot = _prices(date(2026, 1, 1), [100.0, 101.0, 102.0, 103.0, 104.0])
    fut = _prices(date(2026, 2, 1), [105.0, 106.0])  # другие даты
    result = calculate_basis(fut, spot, params={"window": 3})
    assert result.values == []
    assert "note" in result.meta


def test_basis_in_registry():
    assert "basis" in REGISTRY
    assert "futures" in REGISTRY["basis"]["markets"]
