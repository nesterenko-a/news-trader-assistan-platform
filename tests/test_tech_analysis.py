"""Тесты «Теханализ в LLM»: парсер ответа LLM и сборка запроса."""

import json

from app.tech_analysis.llm import ChatGPTClient, resolve_llm
from app.tech_analysis.parser import extract_json_block, parse_response
from app.tech_analysis.confirmation import build_confirmation_chart, closed_candles_label, normalize_confirmation_plans, plans_from_json


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
        '"stop":"98","targets":"110/115","why":"откат","probability":0.65,"rr":2.5}'
        "}\n"
        "```\n"
    )
    parsed = parse_response(md)
    assert parsed["verdict"] == "BUY"
    assert parsed["entry"] == "100-105"
    assert parsed["tp"] == "110/115"
    assert parsed["sl"] == "98"
    assert parsed["scenario_a"]["title"] == "Сценарий A"
    assert parsed["scenario_a"]["probability"] == 0.65
    assert parsed["scenario_a"]["rr"] == 2.5
    # Expected R = 0.65 × 2.5 − 0.35 × 1 = 1.625 − 0.35 = 1.275
    assert parsed["scenario_a"]["expected_r"] == 1.275
    data = json.loads(parsed["scenario_json"])
    assert data["scenario_a"]["stop"] == "98"
    assert data["scenario_a"]["expected_r"] == 1.275


def test_parse_response_probability_percent_string():
    # '~65%' → парсится в 0.65 (нормализация >1 → /100)
    md = "```json\n{\"scenario_b\":{\"probability\":\"~65%\",\"rr\":2.0}}\n```\n"
    parsed = parse_response(md)
    assert parsed["scenario_b"]["probability"] == 0.65
    # Expected R = 0.65 × 2.0 − 0.35 × 1 = 0.95
    assert parsed["scenario_b"]["expected_r"] == 0.95


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
    data = json.loads(parsed["scenario_json"])
    assert data["scenario_a"] == {"expected_r": None}
    assert data["scenario_b"] == {"expected_r": None}
    assert data["scenario_c"] == {"expected_r": None}


def _confirmation_plan(direction="buy"):
    return {
        "direction": direction,
        "timeframe": "1ч",
        "level_label": "зона 100–102",
        "steps": [
            {"kind": "approach", "label": "Подход", "detail": "Цена подходит"},
            {"kind": "close", "label": "Закрытие", "detail": "Свеча закрывается"},
            {"kind": "entry", "label": "Вход", "detail": "После закрытия"},
        ],
        "closed_candles": 1,
        "wait_minutes": None,
        "confirmations": ["объём растёт"],
        "cancellations": ["закрытие ниже зоны"],
    }


def test_confirmation_plan_is_saved_and_reloaded():
    payload = {"verdict": "BUY", "confirmation_plans": {"a": _confirmation_plan()}}
    parsed = parse_response("```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```")
    plans = plans_from_json(parsed["scenario_json"])
    assert plans["a"]["closed_candles"] == 1
    assert plans["a"]["wait_minutes"] is None
    assert plans["a"]["confirmations"] == ["объём растёт"]


def test_confirmation_plan_rejects_invalid_steps_and_keeps_old_records():
    assert plans_from_json('{"scenario_a": {}}') == {}
    assert plans_from_json("broken") == {}
    source = _confirmation_plan()
    source["steps"][1]["kind"] = "script"
    assert normalize_confirmation_plans({"a": source}) == {}
    source = _confirmation_plan()
    source["closed_candles"] = "3"
    source["wait_minutes"] = 0
    plans = normalize_confirmation_plans({"a": source})
    assert plans["a"]["closed_candles"] is None
    assert plans["a"]["wait_minutes"] is None


def test_confirmation_chart_mirrors_sell_and_limits_wait_entry():
    buy = normalize_confirmation_plans({"a": _confirmation_plan("buy")})["a"]
    sell = normalize_confirmation_plans({"a": _confirmation_plan("sell")})["a"]
    buy_chart = build_confirmation_chart(buy)
    sell_chart = build_confirmation_chart(sell)
    assert buy_chart["candles"][2]["body_top"] < buy_chart["level_y"]
    assert sell_chart["candles"][2]["body_top"] >= sell_chart["level_y"]
    wait = _confirmation_plan("wait")
    wait["steps"] = wait["steps"][:2]
    assert normalize_confirmation_plans({"a": wait})["a"]["direction"] == "wait"
    assert normalize_confirmation_plans({"a": _confirmation_plan("wait")}) == {}


def test_confirmation_candle_count_label():
    assert closed_candles_label(1) == "1 закрытая свеча"
    assert closed_candles_label(2) == "2 закрытые свечи"
    assert closed_candles_label(5) == "5 закрытых свечей"
    assert closed_candles_label(11) == "11 закрытых свечей"
    assert closed_candles_label(None) == "не указано"


# --- resolve_llm: выбор провайдера (ChatGPT первично, DeepSeek fallback) ---

class _FakeSettings:
    def __init__(self, chatgpt_key="", chatgpt_model="gpt-4o", chatgpt_base="https://api.openai.com/v1",
                 llm_key="", llm_model="deepseek-reasoner", llm_base="https://api.deepseek.com"):
        self.chatgpt_api_key = chatgpt_key
        self.chatgpt_model = chatgpt_model
        self.chatgpt_base_url = chatgpt_base
        self.chatgpt_request_timeout = 120.0
        self.llm_api_key = llm_key
        self.llm_model = llm_model
        self.llm_base_url = llm_base
        self.llm_request_timeout = 120.0


def _patch_settings(monkeypatch, fake):
    monkeypatch.setattr("app.config.get_settings", lambda: fake)


def _fake_key(provider):
    return f"fake-{provider}"


def test_resolve_llm_prefers_chatgpt(monkeypatch):
    _patch_settings(monkeypatch, _FakeSettings(chatgpt_key=_fake_key("chatgpt"), llm_key=_fake_key("deepseek")))
    r = resolve_llm()
    assert r.api_key == _fake_key("chatgpt")
    assert r.model == "gpt-4o"
    assert "openai.com" in r.base_url


def test_resolve_llm_fallback_deepseek(monkeypatch):
    # CHATGPT_API_KEY пуст — берём LLM_API_KEY (DeepSeek)
    _patch_settings(monkeypatch, _FakeSettings(chatgpt_key="", llm_key=_fake_key("deepseek")))
    r = resolve_llm()
    assert r.api_key == _fake_key("deepseek")
    assert r.model == "deepseek-reasoner"
    assert "deepseek.com" in r.base_url


def test_resolve_llm_no_keys(monkeypatch):
    _patch_settings(monkeypatch, _FakeSettings(chatgpt_key="", llm_key=""))
    r = resolve_llm()
    assert r.api_key == ""
    assert r.model == ""


def test_chatgpt_client_from_settings_fallback(monkeypatch):
    # без CHATGPT_API_KEY from_settings должен идти на DeepSeek
    _patch_settings(monkeypatch, _FakeSettings(chatgpt_key="", llm_key=_fake_key("deepseek")))
    client = ChatGPTClient.from_settings()
    assert client.provider == "deepseek"


def test_resolve_llm_force_chatgpt(monkeypatch):
    # явный choice=chatgpt, даже если есть только DeepSeek -> пусто (ключей ChatGPT нет)
    _patch_settings(monkeypatch, _FakeSettings(chatgpt_key="", llm_key=_fake_key("deepseek")))
    r = resolve_llm("chatgpt")
    assert r.api_key == ""
    # с обоими ключами force-deepseek отдаёт именно DeepSeek
    _patch_settings(monkeypatch, _FakeSettings(chatgpt_key=_fake_key("chatgpt"), llm_key=_fake_key("deepseek")))
    r = resolve_llm("deepseek")
    assert r.api_key == _fake_key("deepseek")
    assert r.model == "deepseek-reasoner"


def test_resolve_llm_force_deepseek_ignores_chatgpt(monkeypatch):
    _patch_settings(monkeypatch, _FakeSettings(chatgpt_key=_fake_key("chatgpt"), llm_key=_fake_key("deepseek")))
    r = resolve_llm("deepseek")
    assert r.api_key == _fake_key("deepseek")
    r2 = resolve_llm("chatgpt")
    assert r2.api_key == _fake_key("chatgpt")

