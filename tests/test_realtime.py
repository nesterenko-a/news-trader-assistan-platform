"""Тесты «Реальное время» (docs/24): сервис realtime, SSE-помощники, админ-блок."""

import pytest
import pytest_asyncio
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import create_session
from app.db.connection import Base
from app.db.models import (
    FuturesTemplate,
    MarketOpenPosition,
    MarketOpenPositionClientGroup,
    RealTimeQuote,
    RealtimeConfig,
    Security,
    User,
    WatchlistItem,
)
from app.graph.service import seed_graph
from app.market.realtime import compute_scope, ensure_config, upsert_quote
from app.api.routes.realtime import _current_quotes, _latest_oi, _oi_event, _quote_event
from app.web.router import admin_page


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as store:
        yield store
    await engine.dispose()


async def _make_request(method: str, token: str | None, path: str, form: dict | None = None):
    headers = []
    if token:
        headers.append((b"cookie", f"nt_token={token}".encode()))
    body = b""
    if form is not None:
        body = "&".join(f"{k}={v}" for k, v in form.items()).encode()
        headers.append((b"content-type", b"application/x-www-form-urlencoded"))

    async def _receive_iterator():
        if body:
            yield {"type": "http.request", "body": body, "more_body": False}
        yield {"type": "http.request", "body": b"", "more_body": False}
        while True:
            yield {"type": "http.disconnect"}

    iterator = _receive_iterator()

    async def receive():
        return await iterator.__anext__()

    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": headers,
            "server": ("test", 80),
            "query_string": b"",
            "client": ("test", 80),
            "scheme": "http",
        },
        receive=receive,
    )


# --- Сервис realtime ---

async def test_ensure_config_creates_singleton(session):
    cfg = await ensure_config(session)
    assert cfg.id == 1
    assert cfg.enabled is False
    assert cfg.interval_quotes_sec == 60
    # повторный вызов не создаёт дубль
    cfg2 = await ensure_config(session)
    assert cfg2.id == cfg.id


async def test_compute_scope_stocks_and_futures(session):
    await seed_graph(session)
    await ensure_config(session)

    sber_row = await session.scalar(select(Security).where(Security.ticker == "SBER"))
    # Фьючерс с уникальным тикером (seed_graph не создаёт фьючерсов)
    session.add(Security(ticker="W4V6", name="SBRF-6.26", security_type="futures", currency="RUB"))
    user = User(username="rtuser", password_hash="x")
    session.add(user)
    await session.flush()
    user_row = await session.scalar(select(User).where(User.username == "rtuser"))
    session.add(WatchlistItem(user_id=user_row.id, security_id=sber_row.id))
    tpl = FuturesTemplate(name="f", tickers=" W4V6 ", kind="futures")
    session.add(tpl)
    await session.commit()

    cfg = await ensure_config(session)
    # без шаблона фьючерсы не обновляются
    stocks, futures = await compute_scope(session, cfg)
    assert "SBER" in [s.ticker for s in stocks]
    assert futures == []

    # с шаблоном — фьючерсы по нему
    tpl_row = await session.scalar(select(FuturesTemplate).where(FuturesTemplate.name == "f"))
    cfg.futures_template_id = tpl_row.id
    await session.commit()
    stocks, futures = await compute_scope(session, cfg)
    assert [f.ticker for f in futures] == ["W4V6"]


async def test_upsert_quote(session):
    await seed_graph(session)
    sber_row = await session.scalar(select(Security).where(Security.ticker == "SBER"))
    await upsert_quote(session, sber_row.id, {"price": 250.5, "open": 249.0, "high": 252.0, "low": 248.0, "volume": 100})
    q = await session.scalar(select(RealTimeQuote).where(RealTimeQuote.security_id == sber_row.id))
    assert q.last == 250.5
    assert q.volume == 100


# --- SSE-помощники ---

async def test_current_quotes_and_quote_event(session):
    await seed_graph(session)
    sber_row = await session.scalar(select(Security).where(Security.ticker == "SBER"))
    await upsert_quote(session, sber_row.id, {"price": 250.5, "open": 249.0, "high": 252.0, "low": 248.0, "volume": 1234})

    quotes = await _current_quotes(session, ["SBER", "AFLT"])
    assert "SBER" in quotes
    assert "AFLT" not in quotes

    ev = _quote_event(sber_row, quotes["SBER"])
    assert "event: quote" in ev
    assert '"ticker": "SBER"' in ev
    assert "250.5" in ev


# --- Админ-блок «Реальное время» ---

async def test_admin_page_renders_realtime_block(session, monkeypatch):
    await seed_graph(session)
    await ensure_config(session)
    admin = User(username="rtadmin", password_hash="x", role="admin")
    session.add(admin)
    await session.flush()
    token = await create_session(session, admin)
    await session.commit()
    monkeypatch.setattr("app.web.router.is_daemon_busy", lambda: True)

    req = await _make_request("GET", token, "/admin")
    resp = await admin_page(req, session)
    html = resp.body.decode()
    assert "Реальное время" in html
    assert "Актуализация рынка (демон)" in html
    assert "realtime_enabled" in html
    assert "Разовые скрипты можно запускать параллельно" in html
    oi_form = html[html.index('value="update_oi"'):html.index('value="realtime_updater"')]
    daemon_form = html[html.index('value="realtime_updater"'):]
    assert "disabled" not in oi_form
    assert "disabled" in daemon_form


async def test_admin_realtime_save_persists(session):
    await seed_graph(session)
    await ensure_config(session)
    admin = User(username="rtadmin", password_hash="x", role="admin")
    session.add(admin)
    await session.flush()
    token = await create_session(session, admin)
    tpl = FuturesTemplate(name="f", tickers="W4V6", kind="futures")
    session.add(tpl)
    await session.commit()
    tpl_row = await session.scalar(select(FuturesTemplate).where(FuturesTemplate.name == "f"))

    from app.web.router import admin_realtime_save

    req = await _make_request(
        "POST",
        token,
        "/admin/realtime/save",
        form={
            "realtime_enabled": "on",
            "interval_quotes_sec": "45",
            "interval_candles_sec": "122",
            "interval_oi_sec": "333",
            "futures_template_id": str(tpl_row.id),
        },
    )
    resp = await admin_realtime_save(req, session)
    assert resp.status_code == 303

    cfg = await session.scalar(select(RealtimeConfig))
    assert cfg.enabled is True
    assert cfg.interval_quotes_sec == 45
    assert cfg.interval_candles_sec == 122
    assert cfg.interval_oi_sec == 333
    assert cfg.futures_template_id == tpl_row.id


async def test_admin_realtime_save_rejects_non_admin(session):
    await seed_graph(session)
    await ensure_config(session)
    plain = User(username="worker", password_hash="x")
    session.add(plain)
    await session.flush()
    token = await create_session(session, plain)
    await session.commit()

    from starlette.exceptions import HTTPException as StarletteHTTPException
    from app.web.router import admin_realtime_save

    req = await _make_request("POST", token, "/admin/realtime/save", form={"realtime_enabled": "on"})
    try:
        await admin_realtime_save(req, session)
        assert False, "Ожидался 403"
    except StarletteHTTPException as exc:
        assert exc.status_code == 403


# --- Демон: регистрация в SCRIPTS и снятый таймаут (docs/24 §10) ---

async def test_realtime_updater_registered_no_timeout():
    from app.admin.runner import get_script, script_timeout_seconds

    script = get_script("realtime_updater")
    assert script is not None
    assert script.get("module") == "scripts.realtime_updater"
    assert script.get("no_timeout") is True
    assert script_timeout_seconds("realtime_updater") is None
    # прочие скрипты не задеты
    assert script_timeout_seconds("update_oi") == 7200


async def test_latest_oi_and_oi_event(session):
    """SSE `oi`-событие: последнее OI фьючерса, изменение к предыдущему дню и группы клиентов."""
    from datetime import date

    await seed_graph(session)
    sec = Security(ticker="W4V6", name="SBRF-6.26", security_type="futures", currency="RUB")
    session.add(sec)
    await session.flush()
    session.add_all(
        [
            MarketOpenPosition(security_id=sec.id, trading_date=date(2026, 8, 27), open_position=3000),
            MarketOpenPosition(security_id=sec.id, trading_date=date(2026, 8, 28), open_position=3030),
            MarketOpenPositionClientGroup(
                security_id=sec.id, trading_date=date(2026, 8, 28), client_group="physical",
                long_pos=500, short_pos=400,
            ),
        ]
    )
    await session.commit()

    oi = await _latest_oi(session, sec.id)
    assert oi is not None
    assert oi["open_position"] == 3030
    assert oi["change_pct"] == pytest.approx(1.0)  # (3030-3000)/3000*100
    assert oi["groups"]["physical"] == {"long": 500, "short": 400, "net": 100}

    ev = _oi_event("W4V6", oi)
    assert "event: oi" in ev
    assert '"ticker": "W4V6"' in ev
    assert "3030" in ev

    # нет данных OI → None
    empty_sec = Security(ticker="zzz", name="z", security_type="futures", currency="RUB")
    session.add(empty_sec)
    await session.flush()
    assert await _latest_oi(session, empty_sec.id) is None
