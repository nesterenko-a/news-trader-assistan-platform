"""Парсинг ответа LLM «Теханализ в LLM».

Ответ LLM — Markdown, в конце которого промт требует обязательный JSON-блок
(см. docs/promt_tech_analize.md §12). Мы извлекаем этот блок и заполняем
verdict/entry/tp/sl + scenario_json для карточек на странице ответа.

Если JSON-блок отсутствует или некорректен — возвращается пустой результат
(анализ всё равно считается успешным, но карточка будет без вердикта/уровней).
"""

import json
import re

_JSON_BLOCK_RE = re.compile(
    r"```json\s*(\{.*?\})\s*```", re.DOTALL
)


def extract_json_block(response_md: str) -> dict | None:
    """Извлекает первый ```json ... ``` блок из ответа."""
    if not response_md:
        return None
    match = _JSON_BLOCK_RE.search(response_md)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError):
        return None


def _to_float(value) -> float | None:
    """Значение LLM (число или строка вроде '~0.65', '65%') → float | None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", ".").replace("%", "").replace("~", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _scenario(data: dict | None, key: str) -> dict:
    src = data.get(key) if isinstance(data, dict) else None
    if not isinstance(src, dict):
        return {}
    prob = _to_float(src.get("probability"))
    rr = _to_float(src.get("rr"))
    # Нормализуем вероятность (0..1): '65%' → 0.65, а '0.65' остаётся
    if prob is not None and prob > 1.0:
        prob = prob / 100.0
    return {
        "title": src.get("title", ""),
        "entry": src.get("entry", ""),
        "stop": src.get("stop", ""),
        "targets": src.get("targets", ""),
        "why": src.get("why", ""),
        "probability": prob,
        "probability_str": src.get("probability", ""),
        "rr": rr,
    }


def expected_r(prob: float | None, rr: float | None) -> float | None:
    """Expected R = prob × rr − (1 − prob) × 1. None, если prob/rr невалидны."""
    if prob is None or rr is None:
        return None
    return prob * rr - (1.0 - prob) * 1.0


def parse_response(response_md: str) -> dict:
    """Разбирает ответ LLM в словарь для карточки-результата.

    Возвращает:
      {"verdict": ..., "entry": ..., "tp": ..., "sl": ...,
       "scenario_json": ... (JSON-строка), "scenario_a": {...}, ...}
    """
    data = extract_json_block(response_md) or {}
    verdict = (data.get("verdict") or "").strip().upper()
    if verdict not in ("BUY", "SELL", "WAIT"):
        verdict = ""
    parsed = {
        "verdict": verdict,
        "entry": (data.get("entry") or "").strip(),
        "tp": (data.get("tp") or "").strip(),
        "sl": (data.get("sl") or "").strip(),
        "scenario_a": _scenario(data, "scenario_a"),
        "scenario_b": _scenario(data, "scenario_b"),
        "scenario_c": _scenario(data, "scenario_c"),
    }
    for key in ("scenario_a", "scenario_b", "scenario_c"):
        sc = parsed[key]
        e = expected_r(sc.get("probability"), sc.get("rr"))
        sc["expected_r"] = round(e, 3) if e is not None else None
    parsed["scenario_json"] = json.dumps(
        {
            "scenario_a": parsed["scenario_a"],
            "scenario_b": parsed["scenario_b"],
            "scenario_c": parsed["scenario_c"],
        },
        ensure_ascii=False,
    )
    return parsed
