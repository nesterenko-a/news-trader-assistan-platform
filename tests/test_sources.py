"""Тесты RSS Feed Manager: SSRF-валидатор, сервис источников, API /v1/sources, страница /news."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import quote

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.routes.sources import (
    add_source,
    check_sources,
    list_sources,
    remove_source,
    restore_defaults,
    search_sources,
    update_source,
)
from app.api.routes.auth import register
from app.db.connection import Base
from app.db.models import Source, User, UserSource
from app.news.feed_check import check_feed, fetch_feed_bytes, validate_feed_url
from app.news.sources_service import (
    SOURCE_CATEGORIES,
    add_default_sources_for_user,
    get_rss_feeds,
    restore_default_sources,
    user_sources,
)
from app.schemas import (
    FeedCheckIn,
    FeedSearchIn,
    RegisterIn,
    SourceIn,
    SourceUpdateIn,
)
from app.web.router import news_page, news_rss_add_selected


PUBLIC_RSS = "https://93.184.216.34/rss"  # публичный IP — без DNS-зависимости в тестах


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


# ---------- SSRF-валидатор ----------

async def test_validate_feed_url_rejects_bad_scheme():
    assert await validate_feed_url("ftp://example.com/rss") is not None
    assert await validate_feed_url("file:///etc/passwd") is not None


async def test_validate_feed_url_rejects_internal():
    assert await validate_feed_url("http://localhost/rss") is not None
    assert await validate_feed_url("http://127.0.0.1/rss") is not None
    assert await validate_feed_url("http://10.0.0.5/rss") is not None
    assert await validate_feed_url("http://192.168.1.10/rss") is not None
    assert await validate_feed_url("http://[::1]/rss") is not None
    assert await validate_feed_url("http://[fe80::1]/rss") is not None  # link-local IPv6


async def test_validate_feed_url_rejects_bad_port():
    assert await validate_feed_url("https://example.com:99999/rss") is not None
    assert await validate_feed_url("https://example.com:abc/rss") is not None


async def test_validate_feed_url_accepts_public():
    assert await validate_feed_url(PUBLIC_RSS) is None


async def test_check_feed_short_circuits_bad_scheme():
    ok, error = await check_feed("ftp://example.com/rss")
    assert not ok
    assert "http" in error


async def test_check_feed_ok_with_mock(monkeypatch):
    rss_xml = (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        "<item><title>Заголовок</title><link>https://example.com/a</link></item>"
        "</channel></rss>"
    ).encode("utf-8")

    response = FakeStreamResponse(chunks=[rss_xml])
    client = FakeStreamClient(response)
    monkeypatch.setattr("app.news.feed_check.httpx.AsyncClient", lambda **kw: client)
    ok, error = await check_feed(PUBLIC_RSS)
    assert ok
    assert error == "ok"


# ---------- Сервис источников ----------

async def test_add_default_sources_for_user(session):
    user = await _mk_user(session)
    added = await add_default_sources_for_user(session, user.id)
    assert added > 0
    rows = await user_sources(session, user.id, kind="rss")
    assert len(rows) == added
    # идемпотентность
    again = await add_default_sources_for_user(session, user.id)
    assert again == 0


async def test_restore_defaults_after_removal(session):
    user = await _mk_user(session)
    await add_default_sources_for_user(session, user.id)
    rows = await user_sources(session, user.id, kind="rss")
    victim = rows[0]
    await session.execute(
        UserSource.__table__.delete().where(
            UserSource.user_id == user.id, UserSource.source_id == victim.id
        )
    )
    await session.commit()
    restored = await restore_default_sources(session, user.id)
    assert restored >= 1
    rows = await user_sources(session, user.id, kind="rss")
    assert any(s.id == victim.id for s in rows)


async def test_get_rss_feeds_filters_active(session):
    session.add_all(
        [
            Source(name="A", kind="rss", config={"url": "https://a.example/rss"}),
            Source(name="B", kind="rss", is_active=False, config={"url": "https://b.example/rss"}),
            Source(name="C", kind="telegram", config={"channel": "c"}),
        ]
    )
    await session.commit()
    feeds = await get_rss_feeds(session)
    assert [f["name"] for f in feeds] == ["A"]


async def test_user_sources_category_filter(session):
    user = await _mk_user(session)
    src = Source(name="Погода", kind="rss", category="погода", config={"url": "https://w.example/rss"})
    session.add(src)
    await session.flush()
    session.add(UserSource(user_id=user.id, source_id=src.id))
    await session.commit()
    rows = await user_sources(session, user.id, kind="rss", category="погода")
    assert [s.name for s in rows] == ["Погода"]
    rows = await user_sources(session, user.id, kind="rss", category="финансы")
    assert rows == []


# ---------- API /v1/sources ----------

async def test_api_add_and_list(session, monkeypatch):
    user = await _mk_user(session)
    monkeypatch.setattr(
        "app.api.routes.sources.check_feed",
        AsyncMock(return_value=(True, "ok")),
    )
    out = await add_source(SourceIn(name="Тест", url=PUBLIC_RSS), user, session)
    assert out["url"] == PUBLIC_RSS
    assert out["last_status"] == "ok"
    items = await list_sources(user=user, session=session)
    assert len(items) == 1
    assert items[0]["name"] == "Тест"


async def test_api_add_rejects_ssrf(session):
    user = await _mk_user(session)
    with pytest.raises(Exception):
        await add_source(
            SourceIn(name="Плохо", url="http://127.0.0.1/rss"), user, session
        )


async def test_api_remove_removes_link_not_catalog(session):
    user = await _mk_user(session)
    src = Source(name="K", kind="rss", config={"url": "https://k.example/rss"})
    session.add(src)
    await session.flush()
    session.add(UserSource(user_id=user.id, source_id=src.id))
    await session.commit()
    result = await remove_source(src.id, user, session)
    assert result["status"] == "ok"
    still = await session.scalar(select(Source).where(Source.id == src.id))
    assert still is not None  # каталог не тронут
    rows = await user_sources(session, user.id, kind="rss")
    assert rows == []


async def test_api_restore_defaults(session):
    user = await _mk_user(session)
    out = await restore_defaults(user, session)
    assert out["added"] > 0


async def test_api_check_sources_updates_status(session, monkeypatch):
    user = await _mk_user(session)
    src = Source(name="C1", kind="rss", config={"url": "https://c1.example/rss"})
    session.add(src)
    await session.flush()
    session.add(UserSource(user_id=user.id, source_id=src.id))
    await session.commit()
    monkeypatch.setattr(
        "app.api.routes.sources.check_feed",
        AsyncMock(return_value=(False, "HTTP 500")),
    )
    out = await check_sources(FeedCheckIn(ids=[src.id]), user, session)
    assert out[0]["last_status"] == "error"
    assert "500" in out[0]["last_error"]


async def test_api_search_parses_llm(session, monkeypatch):
    user = await _mk_user(session)

    class FakeLLM:
        async def chat(self, system, user_prompt):
            return json.dumps(
                [
                    {"name": "Лента А", "url": "https://a.example/rss", "category": "биржа"},
                    {"name": "Лента Б", "url": "https://b.example/rss", "category": "погода"},
                ]
            )

    monkeypatch.setattr("app.api.routes.sources.LLMClient.from_settings", lambda: FakeLLM())
    monkeypatch.setattr(
        "app.api.routes.sources.check_feed",
        AsyncMock(return_value=(True, "ok")),
    )
    out = await search_sources(FeedSearchIn(query="тема"), user, session)
    assert len(out) == 2
    assert out[0]["ok"] is True
    assert out[0]["category"] == "биржа"


def test_search_candidates_parse_flexible():
    from app.api.routes.sources import _parse_candidates

    assert _parse_candidates('[{"name":"A","url":"https://a/rss"}]') == [
        {"name": "A", "url": "https://a/rss"}
    ]
    assert _parse_candidates('```json\n[{"name":"B","url":"https://b/rss"}]\n```') == [
        {"name": "B", "url": "https://b/rss"}
    ]
    assert _parse_candidates("текст без json") == []


# ---------- Веб-страница /news ----------

async def test_news_page_anonymous_redirects(session):
    from starlette.requests import Request

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/news",
            "headers": [],
            "server": ("test", 80),
            "query_string": b"",
            "client": ("test", 80),
            "scheme": "http",
        }
    )
    response = await news_page(request, session)
    assert response.status_code == 303


async def test_news_page_renders_feeds(session):
    from starlette.requests import Request

    user = await _mk_user(session)
    src = Source(
        name="Лента",
        kind="rss",
        category="финансы",
        last_status="ok",
        config={"url": "https://l.example/rss"},
    )
    session.add(src)
    await session.flush()
    session.add(UserSource(user_id=user.id, source_id=src.id))
    await session.commit()

    from app.auth import create_session

    token = await create_session(session, user)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/news",
            "headers": [(b"cookie", f"nt_token={token}".encode())],
            "server": ("test", 80),
            "query_string": b"",
            "client": ("test", 80),
            "scheme": "http",
        }
    )
    response = await news_page(request, session)
    html = response.body.decode()
    assert "Лента" in html
    assert "✔ работает" in html
    assert "Вернуть стандартные ленты" in html
    assert "Найти ленты через ИИ" in html


# ---------- Находки ревизии ----------

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


async def test_fetch_rejects_redirect_to_internal(monkeypatch):
    redirect = FakeStreamResponse(
        status_code=302, headers={"location": "http://127.0.0.1/admin"}
    )
    client = FakeStreamClient(redirect)
    monkeypatch.setattr("app.news.feed_check.httpx.AsyncClient", lambda **kw: client)
    data, error = await fetch_feed_bytes(PUBLIC_RSS)
    assert data is None
    assert "Внутренние" in error or "Локальные" in error


async def test_fetch_rejects_oversized_body(monkeypatch):
    big = FakeStreamResponse(chunks=[b"x" * (1024 * 1024)] * 3)  # > 2 МБ
    client = FakeStreamClient(big)
    monkeypatch.setattr("app.news.feed_check.httpx.AsyncClient", lambda **kw: client)
    data, error = await fetch_feed_bytes(PUBLIC_RSS)
    assert data is None
    assert "слишком большой" in error


async def test_api_add_name_collision_rejected(session, monkeypatch):
    user = await _mk_user(session)
    session.add(Source(name="Занято", kind="rss", config={"url": "https://93.184.216.34/old"}))
    await session.commit()
    monkeypatch.setattr("app.api.routes.sources.check_feed", AsyncMock(return_value=(True, "ok")))
    with pytest.raises(Exception) as excinfo:
        await add_source(SourceIn(name="Занято", url=PUBLIC_RSS), user, session)
    assert "уже существует" in str(excinfo.value)


async def test_api_update_empty_name_rejected(session):
    user = await _mk_user(session)
    src = Source(name="K", kind="rss", config={"url": PUBLIC_RSS})
    session.add(src)
    await session.flush()
    session.add(UserSource(user_id=user.id, source_id=src.id))
    await session.commit()
    with pytest.raises(Exception):
        await update_source(src.id, SourceUpdateIn(name="   "), user, session)


async def test_registration_seeds_default_sources(session):
    out = await register(RegisterIn(username="newuser", password="secret123"), session)
    assert out["username"] == "newuser"
    user = await session.scalar(select(User).where(User.username == "newuser"))
    rows = await user_sources(session, user.id, kind="rss")
    assert len(rows) > 0


async def test_news_add_selected_uses_row_index(session, monkeypatch):
    from starlette.requests import Request

    from app.auth import create_session

    user = await _mk_user(session)
    token = await create_session(session, user)

    calls = []

    async def fake_add(payload, user, session):
        calls.append(payload.url)
        return {}

    monkeypatch.setattr("app.web.router.add_source_api", fake_add)

    body = (
        "cand_count=2"
        "&cand_0_pick=on"
        f"&cand_0_name={quote('Лента А')}"
        f"&cand_0_url={quote(PUBLIC_RSS)}"
        "&cand_0_category=финансы"
        f"&cand_1_name={quote('Лента Б')}"
        f"&cand_1_url={quote('https://93.184.216.34/other')}"
        "&cand_1_category=погода"
    ).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/news/rss/add-selected",
            "headers": [
                (b"cookie", f"nt_token={token}".encode()),
                (b"content-type", b"application/x-www-form-urlencoded"),
            ],
            "server": ("test", 80),
            "query_string": b"",
            "client": ("test", 80),
            "scheme": "http",
        },
        receive=receive,
    )
    response = await news_rss_add_selected(request, session)
    assert response.status_code == 303
    assert calls == [PUBLIC_RSS]  # только выбранная строка 0; строка 1 не добавлена


async def test_news_add_selected_offset_row(session, monkeypatch):
    """Выбор строки 1 без строки 0 — не должен теряться."""
    from starlette.requests import Request

    from app.auth import create_session

    user = await _mk_user(session)
    token = await create_session(session, user)

    calls = []

    async def fake_add(payload, user, session):
        calls.append(payload.url)
        return {}

    monkeypatch.setattr("app.web.router.add_source_api", fake_add)

    body = (
        "cand_count=2"
        f"&cand_0_name={quote('Лента А')}"
        f"&cand_0_url={quote(PUBLIC_RSS)}"
        "&cand_0_category=финансы"
        "&cand_1_pick=on"
        f"&cand_1_name={quote('Лента Б')}"
        f"&cand_1_url={quote('https://93.184.216.34/other')}"
        "&cand_1_category=погода"
    ).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/news/rss/add-selected",
            "headers": [
                (b"cookie", f"nt_token={token}".encode()),
                (b"content-type", b"application/x-www-form-urlencoded"),
            ],
            "server": ("test", 80),
            "query_string": b"",
            "client": ("test", 80),
            "scheme": "http",
        },
        receive=receive,
    )
    response = await news_rss_add_selected(request, session)
    assert response.status_code == 303
    assert calls == ["https://93.184.216.34/other"]  # добавлена строка 1, строка 0 — нет
