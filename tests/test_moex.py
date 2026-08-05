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
