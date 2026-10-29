import asyncio
import codecs
import hashlib
import json
import re
from datetime import datetime, timezone

import httpx
from fastapi import HTTPException

from app.brokers.certificates import tls_context


ENDPOINTS = {
    "production": "https://invest-public-api.tbank.ru/mcp",
    "sandbox": "https://sandbox-invest-public-api.tbank.ru/mcp",
}
VERSIONS = {"2025-11-25", "2025-06-18", "2025-03-26"}
MAX_BYTES = 1024 * 1024
MAX_TOOLS = 300
MANIFEST: dict[str, str] = {}


def failure(status=502, message="MCP вернул некорректный ответ"):
    return HTTPException(status, message)


def schema_digest(schema: dict) -> str:
    return hashlib.sha256(json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


class MCPSession:
    def __init__(self, token: str, environment: str, *, transport=None):
        if environment not in ENDPOINTS:
            raise failure(422, "Выберите контур MCP")
        self.url = ENDPOINTS[environment]
        self._client = httpx.AsyncClient(
            headers={"Authorization": "Bearer " + token, "Accept": "application/json, text/event-stream"},
            timeout=20, follow_redirects=False, verify=tls_context(), transport=transport,
        )
        self._session = None
        self._version = None
        self._counter = 0
        self._approved = set()

    async def __aenter__(self):
        try:
            await self.initialize()
        except BaseException:
            await self._client.aclose()
            raise
        return self

    async def __aexit__(self, *args):
        try:
            if self._session:
                async with asyncio.timeout(3):
                    async with self._client.stream("DELETE", self.url, headers=self._headers()):
                        pass
        except (httpx.HTTPError, TimeoutError):
            pass
        finally:
            self._session = None
            self._approved.clear()
            await self._client.aclose()

    def _headers(self):
        headers = {}
        if self._session:
            headers["MCP-Session-Id"] = self._session
        if self._version:
            headers["MCP-Protocol-Version"] = self._version
        return headers

    def _message(self, raw: str, request_id: int):
        try:
            message = json.loads(raw)
        except (ValueError, TypeError, RecursionError):
            raise failure() from None
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise failure()
        if "method" in message:
            if "id" in message:
                raise failure(502, "MCP запрашивает неподдерживаемое действие клиента")
            return None
        if type(message.get("id")) is not int or message["id"] != request_id:
            raise failure()
        if "error" in message:
            raise failure(502, "MCP отклонил запрос")
        if not isinstance(message.get("result"), dict):
            raise failure()
        return message["result"]

    async def _send(self, method, params=None, *, notification=False):
        self._counter += 1
        request_id = self._counter
        body = {"jsonrpc": "2.0", "method": method}
        if not notification:
            body["id"] = request_id
        if params is not None:
            body["params"] = params
        try:
            async with asyncio.timeout(20):
                async with self._client.stream("POST", self.url, json=body, headers=self._headers()) as response:
                    status = response.status_code
                    if status == 404 and self._session:
                        raise failure(409, "Сессия MCP истекла")
                    if status in (401, 403):
                        raise failure(502, "MCP не принял токен. Проверьте контур и права подключения")
                    if status == 429:
                        raise failure(429, "Превышен лимит запросов MCP")
                    if notification:
                        if status != 202:
                            raise failure()
                        return None
                    if status != 200:
                        raise failure(502, "Сервис MCP недоступен")
                    if method == "initialize":
                        session = response.headers.get("MCP-Session-Id")
                        if session is not None:
                            if not session or len(session) > 1024 or any(not 33 <= ord(c) <= 126 for c in session):
                                raise failure()
                            self._session = session
                    content_type = response.headers.get("content-type", "").split(";")[0].strip()
                    if content_type not in ("application/json", "text/event-stream"):
                        raise failure(502, "MCP вернул неподдерживаемый формат вместо JSON/SSE")
                    data = bytearray()
                    decoder = codecs.getincrementaldecoder("utf-8")()
                    size = 0
                    buffer = ""
                    event = []
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise failure(502, "Ответ MCP превышает допустимый размер")
                        if content_type == "application/json":
                            data.extend(chunk)
                            continue
                        try:
                            buffer += decoder.decode(chunk)
                        except UnicodeDecodeError:
                            raise failure() from None
                        while (separator := re.search(r"\r\n|\r|\n", buffer)):
                            if separator.group() == "\r" and separator.end() == len(buffer):
                                break
                            line, buffer = buffer[:separator.start()], buffer[separator.end():]
                            if not line:
                                if event and any(value.strip() for value in event):
                                    result = self._message("\n".join(event), request_id)
                                    if result is not None:
                                        return result
                                event = []
                            elif line.startswith("data:"):
                                value = line[5:]
                                event.append(value[1:] if value.startswith(" ") else value)
                    if content_type == "application/json":
                        try:
                            result = self._message(data.decode("utf-8"), request_id)
                            if result is None:
                                raise failure()
                            return result
                        except UnicodeError:
                            raise failure() from None
                    if buffer == "\r" and event and any(value.strip() for value in event):
                        result = self._message("\n".join(event), request_id)
                        if result is not None:
                            return result
                    raise failure(502, "Поток MCP завершился без результата")
        except (httpx.TimeoutException, TimeoutError):
            raise failure(504, "Превышено время ожидания MCP") from None
        except httpx.HTTPError:
            raise failure(502, "Не удалось установить защищённое соединение с MCP") from None

    async def initialize(self):
        self._session = None
        self._version = None
        self._approved.clear()
        result = await self._send("initialize", {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "newstrader", "version": "1.0"},
        })
        if result.get("protocolVersion") not in VERSIONS:
            raise failure(502, "Версия протокола MCP не поддерживается")
        if not isinstance(result.get("capabilities"), dict) or not isinstance(result["capabilities"].get("tools"), dict):
            raise failure(502, "Сервер MCP не предоставляет каталог инструментов")
        self._version = result["protocolVersion"]
        await self._send("notifications/initialized", notification=True)

    async def tools(self):
        for attempt in range(2):
            try:
                return await self._tools()
            except HTTPException as exc:
                if exc.status_code != 409 or attempt:
                    raise
                await self.initialize()

    async def _tools(self):
        tools = []
        seen = set()
        cursors = set()
        cursor = None
        for _ in range(20):
            page = await self._send("tools/list", {"cursor": cursor} if cursor else {})
            if not isinstance(page.get("tools"), list):
                raise failure()
            for tool in page["tools"]:
                if not isinstance(tool, dict):
                    raise failure()
                name = tool.get("name")
                schema = tool.get("inputSchema")
                if not isinstance(name, str) or not name or len(name) > 200 or name in seen or not isinstance(schema, dict):
                    raise failure()
                seen.add(name)
                digest = schema_digest(schema)
                approved = MANIFEST.get(name) == digest
                if approved:
                    self._approved.add(name)
                tools.append({"name": name, "schema_sha256": digest, "approved": approved})
                if len(tools) > MAX_TOOLS:
                    raise failure(502, "Каталог MCP превышает допустимый размер")
            cursor = page.get("nextCursor")
            if cursor is None:
                return tools
            if not isinstance(cursor, str) or not cursor or len(cursor) > 4096 or cursor in cursors:
                raise failure()
            cursors.add(cursor)
        raise failure(502, "Превышен предел страниц каталога MCP")


async def inspect_connection(token: str, environment: str):
    try:
        async with asyncio.timeout(120):
            async with MCPSession(token, environment) as client:
                tools = await client.tools()
    except TimeoutError:
        raise failure(504, "Превышено время проверки MCP") from None
    names = ("Портфель и счета", "Поиск ценных бумаг", "Цены, объёмы и стакан", "Новости и сентимент", "Прогнозы аналитиков", "Рыночные аномалии")
    return {
        "available": True, "environment": environment,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "tools": tools,
        "capabilities": [{"name": name, "status": "unavailable", "reason": "Адаптеры инструментов ещё не проверены и не подключены"} for name in names],
    }
