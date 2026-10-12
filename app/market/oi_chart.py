import calendar
from datetime import date
from statistics import median

PERIOD_MONTHS = {"1m": 1, "3m": 3, "6m": 6, "1y": 12, "3y": 36}
PERIOD_LABELS = {"1m": "1м", "3m": "3м", "6m": "6м", "1y": "1г", "3y": "3г", "all": "Всё", "custom": "Свой период"}


def months_before(value: date, months: int) -> date:
    year, month = divmod(value.year * 12 + value.month - 1 - months, 12)
    month += 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def prepare_group_chart(
    series: list[dict],
    group: str = "physical",
    period: str = "1y",
    from_date: date | None = None,
    till_date: date | None = None,
) -> dict:
    if group not in ("physical", "juridical"):
        group = "physical"
    if period not in PERIOD_LABELS:
        period = "1y"
    ordered = sorted(series, key=lambda item: item["date"])
    available = [item for item in ordered if item.get(group) is not None]
    anchor = available[-1]["date"] if available else None
    cutoff = months_before(anchor, PERIOD_MONTHS[period]) if anchor and period in PERIOD_MONTHS else None
    all_points = []
    valid_history = []
    for item in ordered:
        values = item.get(group)
        average = None
        share = None
        if values is not None:
            valid_history.append(values["net"])
            if len(valid_history) >= 30:
                average = sum(valid_history[-30:]) / 30
            total = values["long"] + values["short"]
            share = values["short"] * 100 / total if total else None
        all_points.append({"date": item["date"], "values": values, "average": average, "short_share": share})

    def visible(point: dict) -> bool:
        day = point["date"]
        if period == "custom":
            return (from_date is None or day >= from_date) and (till_date is None or day <= till_date)
        return cutoff is None or day >= cutoff

    points = [point for point in all_points if visible(point)]
    valid = [point for point in points if point["values"] is not None]
    latest = valid[-1] if valid else None
    previous = valid[-2] if len(valid) > 1 else None
    changes = {}
    for key in ("long", "short", "net", "participants_long", "participants_short"):
        current_value = latest["values"].get(key) if latest else None
        previous_value = previous["values"].get(key) if previous else None
        changes[key] = current_value - previous_value if current_value is not None and previous_value is not None else None
    percentile = None
    median_share = None
    percentile_count = 0
    if latest and latest["short_share"] is not None:
        start = months_before(latest["date"], 12)
        shares = [
            point["short_share"]
            for point in all_points
            if start <= point["date"] <= latest["date"] and point["short_share"] is not None
        ]
        percentile_count = len(shares)
        if len(shares) >= 2:
            percentile = sum(value <= latest["short_share"] for value in shares) * 100 / len(shares)
            median_share = median(shares)

    return {
        "group": group,
        "period": period,
        "periods": PERIOD_LABELS,
        "points": points,
        "latest": latest,
        "previous": previous,
        "changes": changes,
        "percentile": percentile,
        "median_share": median_share,
        "percentile_count": percentile_count,
        "first_date": points[0]["date"] if points else None,
        "last_date": points[-1]["date"] if points else None,
        "observation_count": len(valid),
    }


def build_net_chart(points: list[dict], width: int = 900, height: int = 300) -> dict | None:
    if not points:
        return None
    valid_nets = [point["values"]["net"] for point in points if point["values"] is not None]
    if not valid_nets:
        return None
    max_abs = max((abs(value) for value in valid_nets), default=0) or 1
    scale = max_abs * 1.1
    top, bottom = 18, height - 32
    zero_y = (top + bottom) / 2
    half_height = (bottom - top) / 2

    def x_at(index: int) -> float:
        return round(42 + index * (width - 64) / max(len(points) - 1, 1), 1)

    def y_at(value: float) -> float:
        return round(zero_y - value / scale * half_height, 1)

    line_segments = []
    average_segments = []
    area_paths = []
    hit_points = []
    last_valid_index = max((index for index, point in enumerate(points) if point["values"] is not None), default=-1)
    for field, target in (("net", line_segments), ("average", average_segments)):
        segment = []
        for index, point in enumerate(points):
            value = point["values"]["net"] if field == "net" and point["values"] is not None else point["average"] if field == "average" else None
            if value is None:
                if segment:
                    target.append(" ".join(segment))
                    segment = []
                continue
            segment.append(f"{x_at(index)},{y_at(value)}")
        if segment:
            target.append(" ".join(segment))

    for index, point in enumerate(points):
        values = point["values"]
        if values is None:
            continue
        details = f"{point['date']:%d.%m.%Y} · Лонг {values['long']:,} · Шорт {values['short']:,} · Нетто {values['net']:+,} · Средняя 30: {point['average']:,.0f}" if point["average"] is not None else f"{point['date']:%d.%m.%Y} · Лонг {values['long']:,} · Шорт {values['short']:,} · Нетто {values['net']:+,} · Средняя 30: нет данных"
        hit_points.append({"x": x_at(index), "y": y_at(values["net"]), "details": details.replace(",", " "), "last": index == last_valid_index})

    for index in range(1, len(points)):
        left, right = points[index - 1]["values"], points[index]["values"]
        if left is None or right is None:
            continue
        x1, x2 = x_at(index - 1), x_at(index)
        a, b = left["net"], right["net"]
        if (a >= 0 and b >= 0) or (a <= 0 and b <= 0):
            color = "positive" if a + b >= 0 else "negative"
            area_paths.append({"path": f"M{x1},{zero_y} L{x1},{y_at(a)} L{x2},{y_at(b)} L{x2},{zero_y} Z", "side": color})
        else:
            middle = round(x1 + (x2 - x1) * abs(a) / (abs(a) + abs(b)), 1)
            area_paths.append({"path": f"M{x1},{zero_y} L{x1},{y_at(a)} L{middle},{zero_y} Z", "side": "positive" if a > 0 else "negative"})
            area_paths.append({"path": f"M{middle},{zero_y} L{x2},{y_at(b)} L{x2},{zero_y} Z", "side": "positive" if b > 0 else "negative"})

    tick_indexes = sorted({0, len(points) // 3, len(points) * 2 // 3, len(points) - 1})
    return {
        "width": width,
        "height": height,
        "zero_y": zero_y,
        "line_segments": line_segments,
        "average_segments": average_segments,
        "area_paths": area_paths,
        "hit_points": hit_points,
        "ticks": [(y_at(value), f"{value:,.0f}".replace(",", " ")) for value in (-max_abs, -max_abs / 2, 0, max_abs / 2, max_abs)],
        "date_ticks": [(x_at(index), points[index]["date"].strftime("%d.%m.%y")) for index in tick_indexes],
    }
