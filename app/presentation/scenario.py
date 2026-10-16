import html
from datetime import timedelta, timezone

from app.tech_analysis.outcomes import LABELS, utc


def scenario_time(value):
    return utc(value).astimezone(timezone(timedelta(hours=3))).strftime("%d.%m.%Y %H:%M МСК") if value else "—"


def scenario_message(snapshot, event):
    ticker = html.escape(snapshot.ticker)
    label = html.escape(LABELS.get(event.kind, "Вход в диапазон" if event.kind == "zone" else event.kind))
    price = event.data.get("price")
    return f"<b>{ticker} · {snapshot.scenario_key.upper()}</b>\n{label}" + (f" · {price:g}" if isinstance(price, (int, float)) else "") + "\nМодельный сценарий Top-5"
