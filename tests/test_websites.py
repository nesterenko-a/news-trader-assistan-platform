"""Тесты источника «Сайты компаний» (docs/22): LLM-разбор, коллектор, проверка, сервис, API, веб."""

import json
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.routes.sources import add_source, check_sources, search_sources
from app.collectors.websites import fetch_website
from app.db.connection import Base
from app.db.models import Source, User, UserSource
from app.news.feed_check import check_website
from app.news.llm_parse import parse_site_with_llm
from app.news.sources_service import (
    get_website_sources,
    restore_default_sites,
    user_sources,
)
from app.schemas import FeedCheckIn, FeedSearchIn, SourceIn
from app.web.router import news_page, news_site_add, news_site_restore


PUBLIC_SITE = "https://93.184.216.34/press"


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as store:
        yield store
    await engine.dispose()


async def _mk_user(session) -> User:
    user = User(username="tester", password_hash="x")
    session.add(user)
    await session.flush()
    return user


# ---------- LLM-разбор (parse_site_with_llm) ----------

class FakeLLM:
    def __init__(self, raw):
        self.raw = raw

    async def chat(self, system, user_prompt):
        return self.raw


async def test_parse_site_llm_valid_json(monkeypatch):
    monkeypatch.setattr(
        "app.news.llm_parse.LLMClient.from_settings",
        lambda: FakeLLM(
            json.dumps(
                [{"title": "Новость", "link": "https://a.example/1", "description": "Текст", "published": ""}]
            )
        ),
    )
    entries = await parse_site_with_llm(b"<html>list</html>")
    assert entries == [
        {"title": "Новость", "link": "https://a.example/1", "description": "Текст", "published": ""}
    ]


async def test_parse_site_llm_markdown_wrapped(monkeypatch):
    monkeypatch.setattr(
        "app.news.llm_parse.LLMClient.from_settings",
        lambda: FakeLLM('```json\n[{"title":"A","link":"https://a.example/1"}]\n```'),
    )
    entries = await parse_site_with_llm(b"<html></html>")
    assert len(entries) == 1
    assert entries[0]["title"] == "A"


async def test_parse_site_llm_empty_and_garbage(monkeypatch):
    monkeypatch.setattr(
        "app.news.llm_parse.LLMClient.from_settings", lambda: FakeLLM("[]")
    )
    assert await parse_site_with_llm(b"<html></html>") == []
    monkeypatch.setattr(
        "app.news.llm_parse.LLMClient.from_settings", lambda: FakeLLM("нет записей")
    )
    assert await parse_site_with_llm(b"<html></html>") == []


async def test_parse_site_llm_error_returns_empty(monkeypatch):
    class BoomLLM:
        async def chat(self, system, user_prompt):
            raise RuntimeError("llm down")

    monkeypatch.setattr("app.news.llm_parse.LLMClient.from_settings", lambda: BoomLLM())
    assert await parse_site_with_llm(b"<html></html>") == []


# ---------- Коллектор (fetch_website) ----------

async def test_fetch_website_ssrf_rejected():
    articles = await fetch_website(
        {"name": "X", "url": "http://127.0.0.1/press", "use_llm": True}
    )
    assert articles == []


async def test_fetch_website_llm_disabled_returns_empty(monkeypatch):
    client = FakeStreamClient(FakeStreamResponse(chunks=[b"<html>ok</html>"]))
    monkeypatch.setattr("app.news.feed_check.httpx.AsyncClient", lambda **kw: client)
    articles = await fetch_website(
        {"name": "X", "url": PUBLIC_SITE, "use_llm": False}
    )
    assert articles == []


async def test_fetch_website_extracts_via_llm(monkeypatch):
    client = FakeStreamClient(FakeStreamResponse(chunks=[b"<html>list</html>"]))
    monkeypatch.setattr("app.news.feed_check.httpx.AsyncClient", lambda **kw: client)
    monkeypatch.setattr(
        "app.news.llm_parse.parse_site_with_llm",
        AsyncMock(
            return_value=[
                {"title": "Н1", "link": "https://a.example/1", "description": "d", "published": ""}
            ]
        ),
    )
    articles = await fetch_website(
        {"name": "X", "url": PUBLIC_SITE, "use_llm": True}
    )
    assert len(articles) == 1
    assert articles[0].title == "Н1"
    assert articles[0].source_name == "X"


async def test_fetch_website_browser_fallback(monkeypatch):
    client = FakeStreamClient(FakeStreamResponse(status_code=503))
    monkeypatch.setattr("app.news.feed_check.httpx.AsyncClient", lambda **kw: client)
    monkeypatch.setattr(
        "app.news.browser_fetch.fetch_with_playwright",
        AsyncMock(return_value=b"<html>via browser</html>"),
    )
    monkeypatch.setattr(
        "app.news.llm_parse.parse_site_with_llm",
        AsyncMock(return_value=[{"title": "Н2", "link": "https://b.example/1"}]),
    )
    articles = await fetch_website(
        {"name": "X", "url": PUBLIC_SITE, "use_llm": True, "use_browser": True}
    )
    assert len(articles) == 1
    assert articles[0].title == "Н2"


# ---------- Проверка доступности (check_website) ----------

async def test_check_website_ok_without_llm(monkeypatch):
    client = FakeStreamClient(FakeStreamResponse(chunks=[b"<html>ok</html>"]))
    monkeypatch.setattr("app.news.feed_check.httpx.AsyncClient", lambda **kw: client)
    ok, desc = await check_website(PUBLIC_SITE)
    assert ok
    assert desc == "ok"


async def test_check_website_llm_extraction(monkeypatch):
    client = FakeStreamClient(FakeStreamResponse(chunks=[b"<html></html>"]))
    monkeypatch.setattr("app.news.feed_check.httpx.AsyncClient", lambda **kw: client)
    monkeypatch.setattr(
        "app.news.llm_parse.parse_site_with_llm",
        AsyncMock(return_value=[{"title": "A", "link": "https://a.example/1"}]),
    )
    ok, desc = await check_website(PUBLIC_SITE, use_llm=True)
    assert ok
    assert desc == "ok (LLM)"


async def test_check_website_empty_body(monkeypatch):
    client = FakeStreamClient(FakeStreamResponse(chunks=[b""]))
    monkeypatch.setattr("app.news.feed_check.httpx.AsyncClient", lambda **kw: client)
    ok, desc = await check_website(PUBLIC_SITE)
    assert not ok


# ---------- Сервис источников ----------

async def test_get_website_sources_filters_active(session):
    session.add_all(
        [
            Source(name="S1", kind="website", config={"url": "https://s1.example/press"}),
            Source(name="S2", kind="website", is_active=False, config={"url": "https://s2.example/press"}),
            Source(name="F", kind="rss", config={"url": "https://f.example/rss"}),
        ]
    )
    await session.commit()
    sites = await get_website_sources(session)
    assert [s["name"] for s in sites] == ["S1"]
    assert sites[0]["url"] == "https://s1.example/press"


async def test_restore_default_sites_empty(session):
    user = await _mk_user(session)
    added = await restore_default_sites(session, user.id)
    assert added == 0


# ---------- API /v1/sources ----------

async def test_api_add_website(session, monkeypatch):
    user = await _mk_user(session)
    monkeypatch.setattr(
        "app.api.routes.sources.check_website", AsyncMock(return_value=(True, "ok"))
    )
    out = await add_source(
        SourceIn(name="Сайт", url=PUBLIC_SITE, kind="website"), user, session
    )
    assert out["kind"] == "website"
    assert out["last_status"] == "ok"
    rows = await user_sources(session, user.id, kind="website")
    assert len(rows) == 1


async def test_api_check_sources_dispatches_by_kind(session, monkeypatch):
    user = await _mk_user(session)
    src = Source(name="S", kind="website", config={"url": PUBLIC_SITE})
    session.add(src)
    await session.flush()
    session.add(UserSource(user_id=user.id, source_id=src.id))
    await session.commit()
    monkeypatch.setattr(
        "app.api.routes.sources.check_website",
        AsyncMock(return_value=(False, "HTTP 500")),
    )
    out = await check_sources(FeedCheckIn(ids=[src.id]), user, session)
    assert out[0]["last_status"] == "error"
    assert "500" in out[0]["last_error"]


async def test_api_search_rejects_website_kind(session):
    user = await _mk_user(session)
    with pytest.raises(Exception):
        await search_sources(FeedSearchIn(query="тема", kind="website"), user, session)


# ---------- Веб-страница /news (вкладка «Сайты») ----------

def _request(path: str, token: str | None, body: bytes | None = None):
    from starlette.requests import Request

    headers = []
    if token:
        headers.append((b"cookie", f"nt_token={token}".encode()))
    if body is not None:
        headers.append((b"content-type", b"application/x-www-form-urlencoded"))
    scope = {
        "type": "http",
        "method": "POST" if body is not None else "GET",
        "path": path,
        "headers": headers,
        "server": ("test", 80),
        "query_string": b"",
        "client": ("test", 80),
        "scheme": "http",
    }
    if body is not None:
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        return Request(scope, receive=receive)
    return Request(scope)


async def test_news_page_sites_tab(session):
    from app.auth import create_session

    user = await _mk_user(session)
    src = Source(
        name="Пресс",
        kind="website",
        last_status="ok",
        config={"url": "https://p.example/press"},
    )
    session.add(src)
    await session.flush()
    session.add(UserSource(user_id=user.id, source_id=src.id))
    await session.commit()
    token = await create_session(session, user)
    request = _request("/news", token)
    request.scope["query_string"] = b"tab=sites"
    response = await news_page(request, session)
    html = response.body.decode()
    assert "Пресс" in html
    assert "Вернуть стандартные сайты" in html
    assert "Добавить сайт" in html
    assert "Найти ленты через ИИ" not in html


async def test_news_site_add_redirects_with_kind(session, monkeypatch):
    from app.auth import create_session

    user = await _mk_user(session)
    token = await create_session(session, user)
    calls = []

    async def fake_add(payload, user, session):
        calls.append((payload.kind, payload.url))
        return {}

    monkeypatch.setattr("app.web.router.add_source_api", fake_add)
    body = f"name=%D0%A1%D0%B0%D0%B9%D1%82&url={PUBLIC_SITE.replace(':', '%3A').replace('/', '%2F')}&category=".encode()
    response = await news_site_add(_request("/news/site/add", token, body), session)
    assert response.status_code == 303
    assert "tab=sites" in str(response.headers["location"])
    assert calls == [("website", PUBLIC_SITE)]


async def test_news_site_add_invalid_url(session):
    from app.auth import create_session

    user = await _mk_user(session)
    token = await create_session(session, user)
    body = (
        "name=Bad"
        "&url=http%3A%2F%2F127.0.0.1%2Fpress"
        "&category="
    ).encode()
    response = await news_site_add(_request("/news/site/add", token, body), session)
    assert response.status_code == 303
    assert "error=" in str(response.headers["location"])


async def test_news_site_restore_empty(session):
    from app.auth import create_session

    user = await _mk_user(session)
    token = await create_session(session, user)
    response = await news_site_restore(_request("/news/site/restore", token), session)
    assert response.status_code == 303
    assert "tab=sites" in str(response.headers["location"])


# ---------- Хелперы сети ----------

class FakeStreamResponse:
    def __init__(self, status_code=200, chunks=None, headers=None):
        self.status_code = status_code
        self._chunks = chunks or [b"ok"]
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


class FakeStreamClient:
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url):
        self.calls.append(url)
        return self._response
