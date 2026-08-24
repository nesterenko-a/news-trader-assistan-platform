"""Тесты «Теханализ в LLM»: парсер ответа LLM и сборка запроса."""

import json

from app.tech_analysis.llm import ChatGPTClient, resolve_llm
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


def test_resolve_llm_prefers_chatgpt(monkeypatch):
    _patch_settings(monkeypatch, _FakeSettings(chatgpt_key="ck", llm_key="dk"))
    r = resolve_llm()
    assert r.api_key == "ck"
    assert r.model == "gpt-4o"
    assert "openai.com" in r.base_url


def test_resolve_llm_fallback_deepseek(monkeypatch):
    # CHATGPT_API_KEY пуст — берём LLM_API_KEY (DeepSeek)
    _patch_settings(monkeypatch, _FakeSettings(chatgpt_key="", llm_key="dk"))
    r = resolve_llm()
    assert r.api_key == "dk"
    assert r.model == "deepseek-reasoner"
    assert "deepseek.com" in r.base_url


def test_resolve_llm_no_keys(monkeypatch):
    _patch_settings(monkeypatch, _FakeSettings(chatgpt_key="", llm_key=""))
    r = resolve_llm()
    assert r.api_key == ""
    assert r.model == ""


def test_chatgpt_client_from_settings_fallback(monkeypatch):
    # без CHATGPT_API_KEY from_settings должен идти на DeepSeek
    _patch_settings(monkeypatch, _FakeSettings(chatgpt_key="", llm_key="dk"))
    client = ChatGPTClient.from_settings()
    assert client.provider == "deepseek"


def test_resolve_llm_force_chatgpt(monkeypatch):
    # явный choice=chatgpt, даже если есть только DeepSeek -> пусто (ключей ChatGPT нет)
    _patch_settings(monkeypatch, _FakeSettings(chatgpt_key="", llm_key="dk"))
    r = resolve_llm("chatgpt")
    assert r.api_key == ""
    # с обоими ключами force-deepseek отдаёт именно DeepSeek
    _patch_settings(monkeypatch, _FakeSettings(chatgpt_key="ck", llm_key="dk"))
    r = resolve_llm("deepseek")
    assert r.api_key == "dk"
    assert r.model == "deepseek-reasoner"


def test_resolve_llm_force_deepseek_ignores_chatgpt(monkeypatch):
    _patch_settings(monkeypatch, _FakeSettings(chatgpt_key="ck", llm_key="dk"))
    r = resolve_llm("deepseek")
    assert r.api_key == "dk"
    r2 = resolve_llm("chatgpt")
    assert r2.api_key == "ck"

