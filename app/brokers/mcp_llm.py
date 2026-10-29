import asyncio
import hashlib
import hmac
import json
import secrets
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import HTTPException

from app.config import get_settings
from app.tech_analysis.llm import resolve_llm


_SECRET = secrets.token_bytes(32)
_LIMIT = 1024 * 1024


def configuration(provider):
    if provider not in ("deepseek", "chatgpt"):
        raise HTTPException(422, "Выберите DeepSeek или ChatGPT")
    settings = get_settings()
    default = settings.llm_model if provider == "deepseek" else settings.chatgpt_model
    return resolve_llm(provider), default


@asynccontextmanager
async def client_for(config):
    async with httpx.AsyncClient(base_url=config.base_url.rstrip("/") + "/", headers={"Authorization": "Bearer " + config.api_key}, timeout=30, follow_redirects=False) as client:
        yield client


async def request_json(client, method, path, **kwargs):
    try:
        async with client.stream(method, path, **kwargs) as response:
            if response.status_code == 429:
                raise HTTPException(429, "Превышен лимит запросов LLM")
            if response.status_code not in (200, 201):
                raise HTTPException(502, "LLM отклонила запрос. Проверьте ключ, модель и поддержку инструментов")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > _LIMIT:
                    raise HTTPException(502, "Ответ LLM превышает допустимый размер")
            value = json.loads(data)
            if not isinstance(value, dict):
                raise ValueError()
            return value
    except (httpx.TimeoutException, TimeoutError):
        raise HTTPException(504, "Превышено время ожидания LLM") from None
    except (httpx.HTTPError, ValueError, RecursionError):
        raise HTTPException(502, "Не удалось получить корректный ответ LLM") from None


async def catalog(provider):
    config, default = configuration(provider)
    models = [default] if default else []
    warning = None
    if not config.api_key:
        return {"provider": provider, "configured": False, "default_model": default, "models": models, "warning": "Ключ выбранного LLM-провайдера не настроен"}
    try:
        async with asyncio.timeout(10):
            async with client_for(config) as client:
                result = await request_json(client, "GET", "models")
        entries = result.get("data")
        if not isinstance(entries, list) or len(entries) > 2000:
            raise ValueError()
        ids = {row["id"] for row in entries if isinstance(row, dict) and isinstance(row.get("id"), str) and 0 < len(row["id"]) <= 200 and row["id"].isprintable()}
        models += sorted(ids - set(models))
        if not ids:
            raise ValueError()
    except (HTTPException, TimeoutError, ValueError):
        models = [default] if default else []
        warning = "Каталог моделей недоступен. Доступна только модель из настроек; поддержку инструментов нужно проверить"
    return {"provider": provider, "configured": True, "default_model": default, "models": models, "warning": warning}


def _signature(user_id, provider, model, expiry, config):
    data = json.dumps([user_id, provider, model, expiry, config.base_url, config.api_key], separators=(",", ":"))
    return hmac.new(_SECRET, data.encode(), hashlib.sha256).hexdigest()


def verify_check(proof, user_id, provider, model):
    config, _ = configuration(provider)
    try:
        expiry, signature = proof.split(".", 1)
        if not config.api_key or not time.time() < int(expiry) <= time.time() + 901:
            raise ValueError()
        valid = hmac.compare_digest(signature, _signature(user_id, provider, model, expiry, config))
    except (ValueError, AttributeError, TypeError):
        valid = False
    if not valid:
        raise HTTPException(409, "Проверьте выбранную пару провайдер/модель повторно")


async def check(provider, model, user_id):
    config, _ = configuration(provider)
    if not config.api_key:
        raise HTTPException(503, "Ключ выбранного LLM-провайдера не настроен")
    available = await catalog(provider)
    if model not in available["models"]:
        raise HTTPException(422, "Модель отсутствует в доступном каталоге провайдера")
    try:
        async with asyncio.timeout(65):
            async with client_for(config) as client:
                await _probe(client, provider, model)
    except TimeoutError:
        raise HTTPException(504, "Превышено время проверки LLM") from None
    except (KeyError, IndexError, TypeError, ValueError, RecursionError, AttributeError):
        raise HTTPException(502, "Модель не прошла проверку вызова инструментов") from None
    expiry = str(int(time.time()) + 900)
    return {"provider": provider, "model": model, "tool_calling": True, "expires_at": int(expiry), "check_token": expiry + "." + _signature(user_id, provider, model, expiry, config)}


async def _probe(client, provider, model):
    name = "newstrader_connection_probe"
    schema = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    prompt = "Call newstrader_connection_probe once with no arguments. Then return only the exact text returned by the tool."
    if provider == "chatgpt":
        tools = [{"type": "function", "name": name, "description": "Harmless local connectivity test", "parameters": schema, "strict": True}]
        result = await request_json(client, "POST", "responses", json={"model": model, "input": prompt, "tools": tools, "tool_choice": "auto", "store": False, "include": ["reasoning.encrypted_content"], "max_output_tokens": 512})
        calls = [item for item in result["output"] if item.get("type") == "function_call"]
        if len(calls) != 1 or calls[0]["name"] != name or json.loads(calls[0]["arguments"]) != {}:
            raise ValueError()
        output = secrets.token_hex(12)
        items = [{"role": "user", "content": prompt}] + result["output"] + [{"type": "function_call_output", "call_id": calls[0]["call_id"], "output": output}]
        result = await request_json(client, "POST", "responses", json={"model": model, "input": items, "tools": tools, "tool_choice": "none", "store": False, "max_output_tokens": 512})
        text = "".join(part.get("text", "") for item in result["output"] if item.get("type") == "message" for part in item.get("content", []) if part.get("type") == "output_text")
    else:
        tools = [{"type": "function", "function": {"name": name, "description": "Harmless local connectivity test", "parameters": schema}}]
        messages = [{"role": "user", "content": prompt}]
        result = await request_json(client, "POST", "chat/completions", json={"model": model, "messages": messages, "tools": tools, "tool_choice": "auto", "max_tokens": 512})
        message = result["choices"][0]["message"]
        calls = message.get("tool_calls", [])
        if len(calls) != 1 or calls[0].get("type") != "function" or calls[0]["function"]["name"] != name or json.loads(calls[0]["function"]["arguments"]) != {}:
            raise ValueError()
        output = secrets.token_hex(12)
        assistant = {key: message[key] for key in ("role", "content", "tool_calls", "reasoning_content") if key in message}
        messages += [assistant, {"role": "tool", "tool_call_id": calls[0]["id"], "content": output}]
        result = await request_json(client, "POST", "chat/completions", json={"model": model, "messages": messages, "tools": tools, "tool_choice": "none", "max_tokens": 512})
        text = result["choices"][0]["message"].get("content") or ""
    if text.strip() != output:
        raise ValueError()
