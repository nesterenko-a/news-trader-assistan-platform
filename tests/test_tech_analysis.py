"""Тесты «Теханализ в LLM»: парсер ответа LLM и сборка запроса."""

import json

from app.tech_analysis.parser import extract_json_block, parse_response


def test_extract_json_block():
    md = "Текст анализа.\n\n```json\n{\"verdict\": \"BUY\", \"entry\": \"100-105\"}\n```\n"
    data = extract_json_block(md)
    assert data == {"verdict": "BUY", "entry": "100-105"}


def test_extract_json_block_none():
    assert extract_json_block("нет блока") is None
    assert extract_json_block("") is None
    assert extract_json_block("```json\nnot json\n```") is None


def test_parse_response_verdict_buy():
    md = (
        "## Итоговая оценка\n\n"
        "```json\n"
        '{"verdict":"BUY","entry":"100-105","tp":"110/115","sl":"98",'
        '"scenario_a":{"title":"Сценарий A","entry":"100-105",'
        '"stop":"98","targets":"110/115","why":"откат"}'
        "}\n"
        "```\n"
    )
    parsed = parse_response(md)
    assert parsed["verdict"] == "BUY"
    assert parsed["entry"] == "100-105"
    assert parsed["tp"] == "110/115"
    assert parsed["sl"] == "98"
    assert parsed["scenario_a"]["title"] == "Сценарий A"
    data = json.loads(parsed["scenario_json"])
    assert data["scenario_a"]["stop"] == "98"


def test_parse_response_lowercase_verdict():
    md = "```json\n{\"verdict\": \"wait\"}\n```\n"
    parsed = parse_response(md)
    assert parsed["verdict"] == "WAIT"


def test_parse_response_bad_verdict_empties():
    md = "```json\n{\"verdict\": \"HOLD\", \"entry\": \"10\"}\n```\n"
    parsed = parse_response(md)
    assert parsed["verdict"] == ""
    assert parsed["entry"] == "10"


def test_parse_response_no_block():
    parsed = parse_response("просто текст без JSON")
    assert parsed["verdict"] == ""
    assert parsed["entry"] == ""
    assert parsed["scenario_json"] == '{"scenario_a": {}, "scenario_b": {}, "scenario_c": {}}'
