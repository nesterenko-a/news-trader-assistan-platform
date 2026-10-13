import json


STEP_KINDS = frozenset({"approach", "breakout", "close", "retest", "hold", "entry", "invalidation"})
OFFSETS = {
    "approach": -18,
    "breakout": 15,
    "close": 24,
    "retest": 3,
    "hold": 10,
    "entry": 28,
    "invalidation": -25,
}


def _text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    result = value.strip()
    return result[:limit] if result else None


def _positive_int(value: object, maximum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 1 <= value <= maximum else None


def _phrases(value: object) -> list[str] | None:
    if not isinstance(value, list) or len(value) > 6:
        return None
    phrases = [_text(item, 200) for item in value]
    return phrases if all(phrases) else None


def normalize_confirmation_plans(raw: object) -> dict[str, dict]:
    if not isinstance(raw, dict):
        return {}
    plans = {}
    for key in ("a", "b", "c"):
        source = raw.get(key)
        if not isinstance(source, dict):
            continue
        direction = source.get("direction")
        steps_source = source.get("steps")
        if direction not in ("buy", "sell", "wait") or not isinstance(steps_source, list) or not 2 <= len(steps_source) <= 8:
            continue
        steps = []
        for item in steps_source:
            if not isinstance(item, dict) or item.get("kind") not in STEP_KINDS:
                break
            label = _text(item.get("label"), 80)
            detail = _text(item.get("detail"), 240)
            if label is None or detail is None:
                break
            steps.append({"kind": item["kind"], "label": label, "detail": detail})
        has_entry = any(step["kind"] == "entry" for step in steps)
        if len(steps) != len(steps_source) or (direction in ("buy", "sell") and not has_entry) or (direction == "wait" and has_entry):
            continue
        confirmations = _phrases(source.get("confirmations", []))
        cancellations = _phrases(source.get("cancellations", []))
        if confirmations is None or cancellations is None:
            continue
        plans[key] = {
            "direction": direction,
            "timeframe": _text(source.get("timeframe"), 24),
            "level_label": _text(source.get("level_label"), 100),
            "steps": steps,
            "closed_candles": _positive_int(source.get("closed_candles"), 120),
            "wait_minutes": _positive_int(source.get("wait_minutes"), 10080),
            "confirmations": confirmations,
            "cancellations": cancellations,
        }
    return plans


def plans_from_json(value: str | None) -> dict[str, dict]:
    if not value:
        return {}
    try:
        data = json.loads(value)
    except (ValueError, TypeError):
        return {}
    return normalize_confirmation_plans(data.get("confirmation_plans")) if isinstance(data, dict) else {}


def closed_candles_label(value: int | None) -> str:
    if value is None:
        return "не указано"
    ending = value % 10
    suffix = (
        "закрытая свеча" if ending == 1 and value % 100 != 11
        else "закрытые свечи" if ending in (2, 3, 4) and value % 100 not in (12, 13, 14)
        else "закрытых свечей"
    )
    return f"{value} {suffix}"


def build_confirmation_chart(plan: dict) -> dict:
    direction = -1 if plan["direction"] == "sell" else 1
    width = max(560, len(plan["steps"]) * 96 + 48)
    previous = -34 * direction
    candles = []
    for index, step in enumerate(plan["steps"]):
        close = OFFSETS[step["kind"]] * direction
        high = max(previous, close) + 7
        low = min(previous, close) - 7
        x = 62 + index * (width - 124) / max(len(plan["steps"]) - 1, 1)
        top = 82 - max(previous, close)
        bottom = 82 - min(previous, close)
        candles.append({
            "x": round(x, 1),
            "wick_top": 82 - high,
            "wick_bottom": 82 - low,
            "body_top": top,
            "body_height": max(bottom - top, 2),
            "rising": close >= previous,
            "kind": step["kind"],
            "number": index + 1,
        })
        previous = close
    return {"width": width, "height": 155, "level_y": 82, "candles": candles}
