from datetime import date, timedelta

from app.market.oi_chart import build_net_chart, months_before, prepare_group_chart


def _row(day: date, physical: tuple[int, int] | None, juridical: tuple[int, int] | None = None) -> dict:
    def values(pair):
        return {"long": pair[0], "short": pair[1], "net": pair[0] - pair[1], "participants_long": 0, "participants_short": None} if pair is not None else None

    return {"date": day, "physical": values(physical), "juridical": values(juridical)}


def test_chart_periods_average_and_missing_values():
    start = date(2026, 1, 1)
    rows = [_row(start + timedelta(days=i), (i, 15)) for i in range(31)]
    rows[15]["physical"] = None
    result = prepare_group_chart(rows, period="custom", from_date=start + timedelta(days=29))
    assert len(result["points"]) == 2
    assert result["points"][0]["average"] is None
    assert result["points"][1]["average"] == sum(i - 15 for i in range(31) if i != 15) / 30
    assert result["latest"]["values"]["net"] == 15
    assert result["changes"]["net"] == 1
    assert result["latest"]["values"]["participants_long"] == 0
    assert result["latest"]["values"]["participants_short"] is None
    assert result["changes"]["participants_short"] is None


def test_chart_positive_negative_crossing_and_gap():
    start = date(2026, 8, 1)
    rows = [
        _row(start, (10, 20)),
        _row(start + timedelta(days=1), (20, 10)),
        _row(start + timedelta(days=2), None),
        _row(start + timedelta(days=3), (20, 10)),
    ]
    result = prepare_group_chart(rows, period="all")
    chart = build_net_chart(result["points"])
    assert {area["side"] for area in chart["area_paths"]} == {"positive", "negative"}
    assert len(chart["line_segments"]) == 2
    assert len(chart["hit_points"]) == 3
    assert chart["hit_points"][-1]["last"]


def test_chart_group_period_and_percentile():
    start = date(2026, 1, 1)
    rows = [
        _row(start, (10, 10), (30, 10)),
        _row(start + timedelta(days=2), (5, 15), (10, 30)),
    ]
    result = prepare_group_chart(rows, group="juridical", period="all")
    assert result["latest"]["short_share"] == 75
    assert result["percentile"] == 100
    assert result["median_share"] == 50
    assert result["percentile_count"] == 2
    assert prepare_group_chart(rows, group="physical", period="custom", from_date=start + timedelta(days=1))["observation_count"] == 1
    assert prepare_group_chart(rows[:1], period="all")["percentile"] is None
    assert months_before(date(2024, 2, 29), 12) == date(2023, 2, 28)
