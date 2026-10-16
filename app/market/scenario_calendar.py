from datetime import datetime, time, timedelta, timezone

import httpx

MOSCOW = timezone(timedelta(hours=3))


def records(data, block):
    item = data.get(block, {})
    return [dict(zip(item.get("columns", []), row)) for row in item.get("data", [])]


async def schedule(base_url, ticker, is_future, start, stop):
    windows = []
    current = start
    while current.year < stop.year:
        boundary = datetime(current.year + 1, 1, 1, tzinfo=current.tzinfo)
        windows.extend(await _schedule_range(base_url, ticker, is_future, current, boundary - timedelta(microseconds=1)))
        current = boundary
    windows.extend(await _schedule_range(base_url, ticker, is_future, current, stop))
    return windows


async def _schedule_range(base_url, ticker, is_future, start, stop):
    market = "futures" if is_future else "stock"
    base = f"{base_url.rstrip('/')}/calendars/{market}"
    async with httpx.AsyncClient(timeout=15) as client:
        days_response = await client.get(base + ".json", params={"from": start.date().isoformat(), "till": stop.date().isoformat(), "show_all_days": 1, "iss.only": "off_days"})
        days_response.raise_for_status()
        days = {row["tradedate"]: row.get("is_traded") for row in records(days_response.json(), "off_days")}
        windows = []
        offset = 0
        while True:
            response = await client.get(base + "/session.json", params={"from": start.date().isoformat(), "till": stop.date().isoformat(), "iss.only": "session_schedule", "start": offset})
            response.raise_for_status()
            rows = records(response.json(), "session_schedule")
            for row in rows:
                if row.get("secid") not in (None, "", "-", ticker):
                    continue
                if not is_future and row.get("boardid") != "TQBR":
                    continue
                if row.get("type") not in ("system", "main_session", "morning_session", "evening_session", "weekend_session"):
                    continue
                values = []
                for key in ("time_from", "time_till"):
                    value = row.get(key)
                    if not value:
                        break
                    stamp = datetime.fromisoformat(value if len(value) > 10 else f"{row['tradedate']}T{value}")
                    values.append(stamp.replace(tzinfo=MOSCOW).astimezone(timezone.utc))
                if len(values) == 2:
                    if values[1] < values[0]:
                        values[1] += timedelta(days=1)
                    windows.append(tuple(values))
            if not rows:
                break
            offset += len(rows)
            if offset > 100000:
                raise ValueError("Слишком большой ответ календаря")
    day = start.date()
    while day <= stop.date():
        flag = days.get(day.isoformat())
        if flag not in (0, 1):
            raise ValueError("Неполный календарь MOEX")
        if flag == 1 and not any(a.astimezone(MOSCOW).date() == day for a, b in windows):
            raise ValueError("Нет расписания инструмента на торговый день")
        day += timedelta(days=1)
    return windows


def expected_hours(windows, published, stop):
    hours = set()
    for begin, end in windows:
        cursor = begin.replace(minute=0, second=0, microsecond=0)
        while cursor < end:
            if cursor > published and cursor + timedelta(hours=1) <= stop:
                hours.add(cursor)
            cursor += timedelta(hours=1)
    return hours


def market_context(windows, now):
    active = any(begin <= now <= end for begin, end in windows)
    expected = expected_hours(windows, datetime.combine(now.date() - timedelta(days=40), time.min, timezone.utc), now)
    return {"session": "open" if active else "closed", "expected_last": max(expected).isoformat() if expected else None}
