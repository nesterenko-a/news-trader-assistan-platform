from datetime import datetime, timedelta, timezone

import pytest

from app.market.t_invest_calendar import CalendarDataError, calendar_windows

T = datetime(2026, 10, 16, tzinfo=timezone.utc)


def period(kind, begin, end):
    return {"type": kind, "interval": {"startTs": (T + timedelta(hours=begin)).isoformat(), "endTs": (T + timedelta(hours=end)).isoformat()}}


def payload(intervals=None, trading=True):
    return {"exchanges": [{"exchange": "MOEX_EXTENDED", "days": [{"date": T.isoformat(), "isTradingDay": trading, "intervals": intervals or []}]}]}


def test_broker_calendar_excludes_dealer_and_generic_duplicates():
    raw = payload([
        period("dealer_regular_trading_session", 0, 4),
        period("regular_trading_session", 0, 12),
        period("regular_trading_session_main", 6, 12),
        period("clearing", 12, 13),
    ])
    assert calendar_windows(raw, "moex_extended", T, T) == [(T + timedelta(hours=6), T + timedelta(hours=12))]
    assert calendar_windows(payload(trading=False), "MOEX_EXTENDED", T, T) == []


@pytest.mark.parametrize("raw,stop", [
    (payload(), T),
    (payload(trading=False), T + timedelta(days=1)),
    (payload([period("unknown_session", 6, 12)]), T),
    (payload([period("regular_trading_session_main", 6, 12)], trading=False), T),
    ({"exchanges": []}, T),
    (payload([period("dealer_regular_trading_session", 0, 4)]), T),
])
def test_broker_calendar_unknown_coverage_never_closes_day(raw, stop):
    with pytest.raises(CalendarDataError):
        calendar_windows(raw, "MOEX_EXTENDED", T, stop)


def test_broker_calendar_weekend_and_year_boundary():
    raw = payload([period("holiday_opening_auction_morning", 6, 7), period("holiday_trading_session_morning", 7, 16)])
    assert len(calendar_windows(raw, "MOEX_EXTENDED", T, T)) == 2
    raw["exchanges"][0]["days"][0]["date"] = "2026-12-31T00:00:00Z"
    with pytest.raises(CalendarDataError):
        calendar_windows(raw, "MOEX_EXTENDED", datetime(2026, 12, 31, tzinfo=timezone.utc), datetime(2027, 1, 1, tzinfo=timezone.utc))


@pytest.mark.asyncio
async def test_calendar_lookup_uses_instrument_exchange_and_selected_host():
    import httpx
    from app.brokers.t_invest import TInvestClient
    def respond(request):
        import json
        assert request.url.host == "sandbox-invest-public-api.tbank.ru"
        body = json.loads(request.content)
        if request.url.path.endswith("GetInstrumentBy"):
            assert body["classCode"] == "SPBFUT" and body["id"] == "IRZ6"
            return httpx.Response(200, json={"instrument": {"ticker": "IRZ6", "realExchange": "REAL_EXCHANGE_MOEX", "exchange": "FORTS_FUTURES_WEEKEND"}})
        assert body["exchange"] == "FORTS_FUTURES_WEEKEND"
        assert body["from"] == T.isoformat()
        return httpx.Response(200, json={"exchanges": []})
    client = TInvestClient("test", httpx.MockTransport(respond), environment="sandbox")
    instrument, raw = await client.trading_calendar("IRZ6", True, T + timedelta(hours=10), T + timedelta(hours=12))
    assert instrument["exchange"] == "FORTS_FUTURES_WEEKEND"
    assert raw == {"exchanges": []}
