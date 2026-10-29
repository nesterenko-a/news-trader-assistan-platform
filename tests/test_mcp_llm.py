import json
import secrets
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import HTTPException

from app.brokers import mcp_llm
from app.tech_analysis.llm import ResolvedLlm


@pytest.fixture
def provider(monkeypatch):
    config = ResolvedLlm("private-llm-key", "https://test.invalid/v1", "configured", 30)
    monkeypatch.setattr(mcp_llm, "configuration", lambda p: (config, "configured"))
    requests = []

    def install(handler):
        @asynccontextmanager
        async def factory(settings):
            async def dispatch(request):
                requests.append(request)
                return handler(request)
            async with httpx.AsyncClient(base_url="https://test.invalid/v1/", transport=httpx.MockTransport(dispatch)) as client:
                yield client
        monkeypatch.setattr(mcp_llm, "client_for", factory)
    return config, requests, install


def response(request, *, bad=False, wrong_result=False):
    if request.method == "GET":
        return httpx.Response(200, json={"data": [{"id": "catalog-model"}]})
    data = json.loads(request.content)
    assert data["model"] == "catalog-model"
    assert "broker" not in data
    name = "unauthorized" if bad else "newstrader_connection_probe"
    if request.url.path.endswith("responses"):
        assert data["store"] is False
        if isinstance(data["input"], str):
            return httpx.Response(200, json={"output": [{"type": "function_call", "call_id": "call-test", "name": name, "arguments": "{}"}]})
        text = "wrong" if wrong_result else data["input"][-1]["output"]
        return httpx.Response(200, json={"output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]})
    if len(data["messages"]) == 1:
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": None, "reasoning_content": "test reasoning", "tool_calls": [{"id": "call-test", "type": "function", "function": {"name": name, "arguments": "{}"}}]}}]})
    assert data["messages"][1]["reasoning_content"] == "test reasoning"
    text = "wrong" if wrong_result else data["messages"][-1]["content"]
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


@pytest.mark.parametrize("name", ["deepseek", "chatgpt"])
async def test_tool_round_trip_and_bound_verification(provider, name):
    config, requests, install = provider
    install(response)
    result = await mcp_llm.check(name, "catalog-model", 7)
    assert result["tool_calling"] is True
    assert len(requests) == 3
    assert "private-llm-key" not in json.dumps(result)
    mcp_llm.verify_check(result["check_token"], 7, name, "catalog-model")
    for args in [(8, name, "catalog-model"), (7, name, "different"), (7, "chatgpt" if name == "deepseek" else "deepseek", "catalog-model")]:
        with pytest.raises(HTTPException):
            mcp_llm.verify_check(result["check_token"], *args)
    config.api_key = secrets.token_urlsafe(24)
    with pytest.raises(HTTPException):
        mcp_llm.verify_check(result["check_token"], 7, name, "catalog-model")


@pytest.mark.parametrize("name", ["deepseek", "chatgpt"])
@pytest.mark.parametrize("bad,wrong_result", [(True, False), (False, True)])
async def test_incompatible_tool_or_result_rejected(provider, name, bad, wrong_result):
    _, requests, install = provider
    install(lambda r: response(r, bad=bad, wrong_result=wrong_result))
    with pytest.raises(HTTPException) as error:
        await mcp_llm.check(name, "catalog-model", 7)
    assert error.value.status_code == 502
    assert len(requests) == (2 if bad else 3)


async def test_catalog_failure_falls_back_only_to_configured_model(provider):
    _, _, install = provider
    install(lambda r: httpx.Response(401, text="private-llm-key"))
    result = await mcp_llm.catalog("deepseek")
    assert result["models"] == ["configured"]
    assert result["warning"]
    assert "private" not in json.dumps(result)


async def test_unavailable_model_never_sent_to_llm(provider):
    _, requests, install = provider
    install(response)
    with pytest.raises(HTTPException) as error:
        await mcp_llm.check("chatgpt", "made-up", 1)
    assert error.value.status_code == 422
    assert len(requests) == 1


async def test_missing_key_no_network(provider):
    config, requests, install = provider
    config.api_key = ""
    install(response)
    assert (await mcp_llm.catalog("deepseek"))["configured"] is False
    with pytest.raises(HTTPException) as error:
        await mcp_llm.check("deepseek", "configured", 7)
    assert error.value.status_code == 503
    assert requests == []


@pytest.mark.parametrize("status", [301, 400, 429, 500])
async def test_provider_errors_sanitized(provider, status):
    _, _, install = provider
    def handler(request):
        return response(request) if request.method == "GET" else httpx.Response(status, text="private-llm-key")
    install(handler)
    with pytest.raises(HTTPException) as error:
        await mcp_llm.check("deepseek", "catalog-model", 7)
    assert error.value.status_code == (429 if status == 429 else 502)
    assert "private" not in error.value.detail


@pytest.mark.parametrize("proof", [None, "invalid", "0.signature", "999999999999.signature"])
def test_invalid_or_expired_proof(provider, proof):
    with pytest.raises(HTTPException):
        mcp_llm.verify_check(proof, 7, "deepseek", "configured")
