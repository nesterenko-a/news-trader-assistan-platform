import hashlib
import json
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from app.brokers.t_invest import TInvestClient, decrypt_token

TRADING_TYPES = frozenset({
    "opening_auction", "opening_auction_morning", "opening_auction_main", "opening_auction_evening",
    "closing_auction", "closing_auction_main", "closing_auction_evening",
    "regular_trading_session_morning", "regular_trading_session_main", "regular_trading_session_evening",
    "holiday_opening_auction_morning", "holiday_trading_session_morning",
})
NON_TRADING_TYPES = frozenset({"regular_trading_session", "clearing", "break", "evening_clearing", "day_clearing"})


class CalendarDataError(ValueError):
    pass


def stamp(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise CalendarDataError("Т-Инвестиции вернули расписание без часового пояса")
    return result.astimezone(timezone.utc)


def calendar_windows(raw, exchange, start, stop):
    exchanges = [row for row in raw.get("exchanges", []) if row.get("exchange", "").casefold() == exchange.casefold()]
    if len(exchanges) != 1:
        raise CalendarDataError("Т-Инвестиции не подтвердили расписание площадки")
    days = {}
    for row in exchanges[0].get("days", []):
        day = stamp(row["date"]).date()
        if day in days:
            raise CalendarDataError("Т-Инвестиции вернули противоречивый календарь")
        days[day] = row
    windows = set()
    day = start.date()
    while day <= stop.date():
        row = days.get(day)
        if row is None or not isinstance(row.get("isTradingDay"), bool):
            raise CalendarDataError("Календарь Т-Инвестиций не покрывает всё окно наблюдения")
        intervals = row.get("intervals") or []
        trading = []
        for item in intervals:
            kind = item.get("type", "")
            if kind.startswith("dealer_") or kind in NON_TRADING_TYPES:
                continue
            if kind not in TRADING_TYPES:
                raise CalendarDataError("Неизвестный тип сессии Т-Инвестиций; расписание требует проверки")
            bounds = item.get("interval") or {}
            begin, end = stamp(bounds["startTs"]), stamp(bounds["endTs"])
            if begin >= end:
                raise CalendarDataError("Т-Инвестиции вернули неверные границы сессии")
            trading.append((begin, end))
        if row["isTradingDay"] and not trading:
            raise CalendarDataError("Т-Инвестиции не подтвердили биржевые интервалы торгового дня")
        if not row["isTradingDay"] and trading:
            raise CalendarDataError("Т-Инвестиции вернули противоречивый календарь")
        windows.update(trading)
        day += timedelta(days=1)
    return sorted(windows)


async def schedule(connection, ticker, is_future, start, stop, now):
    try:
        client = TInvestClient(decrypt_token(connection), environment=connection.environment)
        instrument, raw = await client.trading_calendar(ticker, is_future, start, stop)
        windows = calendar_windows(raw, instrument["exchange"], start, stop)
    except HTTPException as exc:
        raise CalendarDataError("Календарь Т-Инвестиций недоступен; проверьте подключение и контур брокера") from exc
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        if isinstance(exc, CalendarDataError):
            raise
        raise CalendarDataError("Т-Инвестиции вернули неполное расписание") from exc
    metadata = {
        "provider": "t_invest", "environment": connection.environment,
        "exchange": instrument["exchange"], "fetched_at": now.isoformat(),
        "sha256": hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest(),
        "raw": raw,
    }
    return windows, metadata
