import asyncio
from datetime import date


from app.market.moex import _cursor_total


def test_cursor_total_present():
    data = {
        "history.cursor": {
            "columns": ["INDEX", "TOTAL", "PAGESIZE"],
            "data": [[0, 200, 100]],
        }
    }
    assert _cursor_total(data, "history") == 200


def test_cursor_total_missing():
    assert _cursor_total({}, "history") is None


def test_cursor_total_empty_rows():
    data = {"history.cursor": {"columns": ["TOTAL"], "data": []}}
    assert _cursor_total(data, "history") is None


def test_candles_url():
    from app.market.moex import candles_url

    stock = candles_url("AFLT")
    fut = candles_url("W4V6", "futures")
    fut_custom = candles_url("W4V6", "futures", "https://iss.moex.com/iss")
    # Обязательный /iss/ в пути (без него MOEX отвечает 302)
    assert stock.startswith("https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/")
    assert "securities/AFLT/candles.json" in stock
    assert fut.startswith("https://iss.moex.com/iss/engines/futures/markets/forts/")
    assert "securities/W4V6/candles.json" in fut
    assert fut == fut_custom
    assert "/iss/iss/" not in fut


async def test_fetch_candles_retries_on_read_error(monkeypatch):
    """Обрыв соединения (httpx.ReadError) — ретраи, затем успешный ответ."""
    import httpx

    from app.market import moex

    class FakeResp:
        status_code = 200

        def json(self):
            return {"candles": {"columns": ["begin", "close"], "data": []}}

    class FakeClient:
        calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url, params=None):
            FakeClient.calls += 1
            if FakeClient.calls <= 2:
                raise httpx.ReadError("connection reset")
            return FakeResp()

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(moex.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(moex.httpx, "AsyncClient", lambda **kw: FakeClient())
    candles = await moex.MOEXClient().fetch_candles(
        "SBER", date(2026, 1, 1), date(2026, 1, 10)
    )
    assert candles == []
    assert FakeClient.calls == 3


async def test_fetch_candles_skips_after_all_retries(monkeypatch):
    """Постоянный обрыв соединения — fetch_candles возвращает [] (не падает, не виснет)."""
    import httpx

    from app.market import moex

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url, params=None):
            raise httpx.ReadError("connection reset")

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(moex.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(moex.httpx, "AsyncClient", lambda **kw: FakeClient())
    candles = await moex.MOEXClient().fetch_candles(
        "SBER", date(2026, 1, 1), date(2026, 1, 10)
    )
    assert candles == []


async def test_fetch_candles_retries_on_cancel_timeout(monkeypatch):
    """Внутренняя отмена из-за таймаута (CancelledError без cancelling()) — ретрай, затем успех."""
    from app.market import moex

    class FakeResp:
        status_code = 200

        def json(self):
            return {"candles": {"columns": ["begin", "close"], "data": []}}

    class FakeClient:
        calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url, params=None):
            FakeClient.calls += 1
            if FakeClient.calls <= 2:
                raise asyncio.CancelledError()
            return FakeResp()

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(moex.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(moex.httpx, "AsyncClient", lambda **kw: FakeClient())
    candles = await moex.MOEXClient().fetch_candles(
        "SBER", date(2026, 1, 1), date(2026, 1, 10)
    )
    assert candles == []
    assert FakeClient.calls == 3


async def test_fetch_candles_re_raises_external_cancel(monkeypatch):
    """Внешняя отмена задачи (task.cancel) — CancelledError пробрасывается, ретраи не глотают."""
    import httpx

    from app.market import moex

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url, params=None):
            raise asyncio.CancelledError()

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(moex.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(moex.httpx, "AsyncClient", lambda **kw: FakeClient())

    task = asyncio.create_task(
        moex.MOEXClient().fetch_candles("SBER", date(2026, 1, 1), date(2026, 1, 10))
    )
    await asyncio.sleep(0)
    task.cancel()
    try:
        await task
        raised = False
    except asyncio.CancelledError:
        raised = True
    assert raised
