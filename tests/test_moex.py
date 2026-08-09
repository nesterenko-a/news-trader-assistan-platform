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
