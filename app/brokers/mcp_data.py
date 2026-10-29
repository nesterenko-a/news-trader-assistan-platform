from datetime import datetime, timezone
from decimal import Decimal, DecimalException
from statistics import median
from urllib.parse import urlsplit


def number(value):
    try:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, dict):
            if "units" not in value and "nano" not in value:
                return None
            units, nano = str(value.get("units", 0)), str(value.get("nano", 0))
            if max(len(units), len(nano)) > 100:
                return None
            units, nano = Decimal(units), Decimal(nano)
            if not all(part.is_finite() and -40 <= part.adjusted() <= 40 for part in (units, nano)):
                return None
            result = units + nano / Decimal(10**9)
        else:
            if len(str(value)) > 100:
                return None
            result = Decimal(str(value))
        return result if result.is_finite() and -30 <= result.adjusted() <= 30 else None
    except (DecimalException, ValueError, TypeError):
        return None


def calculations(name, data):
    result, warnings = {}, []
    if not isinstance(data, dict):
        return result, warnings
    if name == "orderbook":
        sides = []
        invalid = False
        for key in ("bids", "asks"):
            rows = data.get(key, [])
            if not isinstance(rows, list) or any(not isinstance(row, dict) or number(row.get("price")) is None or number(row.get("price")) <= 0 or number(row.get("quantity")) is None or number(row.get("quantity")) <= 0 for row in rows):
                invalid = True
            prices = [number(row.get("price")) for row in rows if isinstance(row, dict) and (number(row.get("quantity")) or 0) > 0] if isinstance(rows, list) else []
            sides.append([p for p in prices if p is not None and p > 0])
        if invalid or not all(sides) or min(sides[1]) < max(sides[0]):
            warnings.append("Спред не рассчитан: стакан пуст, односторонний или некорректный")
        else:
            bid, ask = max(sides[0]), min(sides[1])
            spread = ask - bid
            relative = spread / ((ask + bid) / 2) * 100
            result = {"best_bid": str(bid), "best_ask": str(ask), "spread": str(spread), "spread_pct": str(relative), "price_units": "единицы исходной котировки, не обязательно рубли", "wide_spread_threshold_pct": "1", "wide_spread": relative >= 1, "formula": "(ask-bid)/((ask+bid)/2)*100"}
    elif name == "daily_candles":
        rows = data.get("candles", [])
        complete = {}
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict) or row.get("isComplete", row.get("is_complete")) is not True:
                    continue
                try:
                    date = datetime.fromisoformat(row.get("time", "").replace("Z", "+00:00")).date()
                except (ValueError, TypeError, AttributeError):
                    continue
                if date <= datetime.now(timezone.utc).date():
                    complete[date] = row
        ordered = [row for _, row in sorted(complete.items())][-30:]
        result["completed_days"] = len(ordered)
        if ordered:
            result["as_of"] = ordered[-1].get("time")
        if len(ordered) >= 21:
            previous = [number(row.get("volume")) for row in ordered[-21:-1]]
            last = number(ordered[-1].get("volume"))
            if all(v is not None and v >= 0 for v in previous) and last is not None and last >= 0:
                baseline = median(previous)
                if baseline > 0:
                    result.update({"volume_ratio": str(last / baseline), "volume_threshold": "3", "volume_anomaly": last >= 3 * baseline})
        if len(ordered) >= 2:
            previous, last = number(ordered[-2].get("close")), number(ordered[-1].get("close"))
            if previous is not None and previous > 0 and last is not None and last >= 0:
                change = (last / previous - 1) * 100
                result.update({"change_pct": str(change), "price_threshold_pct": "5", "price_anomaly": abs(change) >= 5})
        if "volume_ratio" not in result:
            warnings.append("Аномалия объёма не рассчитана: нужны 21 завершённый день и ненулевая медиана предыдущих 20 дней")
        warnings.append("Неполные и будущие дни исключены; завершённость взята из isComplete источника. Корпоративные корректировки не подтверждены; признаки не являются прогнозом")
    elif name == "portfolio":
        total = data.get("totalAmountPortfolio", {})
        amount = number(total)
        currency = total.get("currency") if isinstance(total, dict) else None
        result = {"allocation": []}
        if amount is not None and currency:
            result.update({"total": str(amount), "currency": currency})
        rows = data.get("positions", [])
        if isinstance(rows, list) and amount is not None and amount > 0 and currency:
            for row in rows[:100]:
                if not isinstance(row, dict):
                    continue
                price, quantity = number(row.get("currentPrice")), number(row.get("quantity"))
                money = row.get("currentPrice", {})
                kind = row.get("instrumentType")
                if kind not in ("share", "bond", "etf", "currency") or not isinstance(money, dict) or money.get("currency") != currency or price is None or quantity is None:
                    warnings.append("Не все доли рассчитаны: для части позиций нет сопоставимой оценки и валюты либо это производный инструмент")
                    continue
                accrued = number(row.get("currentNkd"))
                if kind == "bond" and (accrued is None or not isinstance(row.get("currentNkd"), dict) or row["currentNkd"].get("currency") != currency):
                    warnings.append("Доля облигации не рассчитана: нет корректного НКД в валюте портфеля")
                    continue
                value = (price + (accrued if kind == "bond" else Decimal(0))) * quantity
                result["allocation"].append({"instrument": row.get("instrumentUid", row.get("figi", "")), "value": str(value), "share_pct": str(value / amount * 100)})
        else:
            warnings.append("Доли не рассчитаны: нужна положительная стоимость портфеля и сопоставимая валюта")
    return result, list(dict.fromkeys(warnings))


def bounded_data(value, limit, warnings, depth=0):
    if depth > 12:
        warnings.append("Глубокие вложения ответа исключены")
        return None
    if isinstance(value, dict):
        return {key: bounded_data(child, limit, warnings, depth + 1) for key, child in value.items() if key not in {"accountId", "account_id", "userId", "user_id", "brokerAccountId", "token", "authorization", "sessionId"}}
    if isinstance(value, list):
        if len(value) > limit:
            warnings.append(f"Выборка усечена до {limit} записей; это не весь рынок/портфель")
        return [bounded_data(child, limit, warnings, depth + 1) for child in value[:limit]]
    if isinstance(value, str) and len(value) > 4000:
        warnings.append("Длинный текст источника усечён")
        return value[:4000]
    return value


def safe_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password:
            return value
    except ValueError:
        pass
    return None
