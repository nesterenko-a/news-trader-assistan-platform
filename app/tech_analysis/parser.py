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


def _scenario(data: dict | None, key: str) -> dict:
    src = data.get(key) if isinstance(data, dict) else None
    if not isinstance(src, dict):
        return {}
    return {
        "title": src.get("title", ""),
        "entry": src.get("entry", ""),
        "stop": src.get("stop", ""),
        "targets": src.get("targets", ""),
        "why": src.get("why", ""),
    }


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
    parsed["scenario_json"] = json.dumps(
        {
            "scenario_a": parsed["scenario_a"],
            "scenario_b": parsed["scenario_b"],
            "scenario_c": parsed["scenario_c"],
        },
        ensure_ascii=False,
    )
    return parsed
