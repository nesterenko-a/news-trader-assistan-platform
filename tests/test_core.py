from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.alerts.service import (
    get_settings,
    load_alerts,
    mark_read,
    process_alerts,
    update_settings,
)
from app.auth import (
    create_session,
    delete_session,
    get_current_user,
    hash_password,
    verify_password,
)
from app.db.connection import Base
from app.collectors.rss import _parse_date
from app.db.models import (
    Alert,
    Article,
    ArticleEntity,
    Entity,
    MacroEvent,
    PortfolioPosition,
    Security,
    Session,
    Source,
    Strategy,
    TelegramLinkCode,
    User,
    WatchlistItem,
    macro_event_security,
)
from app.macro.service import event_tickers, list_events, list_security_events
from app.web.router import macro_page
from app.bot.linking import (
    consume_link_code,
    create_link_code,
    set_user_chat,
    unlink_telegram,
)
from app.alerts.delivery import deliver_telegram
from scripts.collect_news import _mention_check, _parse_since, _within_since
from app.graph.service import (
    find_influence_paths,
    resolve_entity_id,
    seed_graph,
    security_entity_ids,
)
from app.strategy.engine import generate_strategy


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as store:
        yield store
    await engine.dispose()


async def test_seed_and_influence_paths(session):
    await seed_graph(session)

    oil_id = await resolve_entity_id(session, "Нефть")
    aero_id = await resolve_entity_id(session, "Аэрофлот")
    assert oil_id is not None
    assert aero_id is not None

    aero_entities = await security_entity_ids(session, 1)
    assert aero_id in aero_entities

    paths = await find_influence_paths(session, oil_id, aero_id)
    assert paths
    best = paths[0]
    assert best.sign == -1.0
    assert best.entities[0] == "Нефть"
    assert best.entities[-1] == "Аэрофлот"

    lukoil_id = await resolve_entity_id(session, "Лукойл")
    oil_paths = await find_influence_paths(session, oil_id, lukoil_id)
    assert oil_paths
    assert oil_paths[0].sign == 1.0


class FakeMOEX:
    async def fetch_quote(self, ticker: str):
        return {
            "ticker": ticker,
            "price": 100.0,
            "open": 100.0,
            "high": 105.0,
            "low": 98.0,
            "volume": 1000,
        }

    async def fetch_daily_closes(self, ticker: str, days: int = 60):
        return [
            100.2, 99.8, 100.5, 100.1, 99.6, 100.8, 100.3, 99.9, 100.4, 100.0,
            99.7, 100.6, 100.2, 99.8, 100.5, 100.1, 99.9, 100.7, 100.2, 100.0,
            99.8, 100.4, 100.1, 99.7, 100.6, 100.3, 99.9, 100.5, 100.2, 100.1,
        ]


async def _store_news(session, url: str, entity_name: str, sentiment: str):
    source = Source(name="РБК", kind="rss", reputation_score=0.8)
    session.add(source)
    await session.flush()
    entity_id = await resolve_entity_id(session, entity_name)
    article = Article(
        title=f"{entity_name} {sentiment}",
        text="текст новости",
        url=url,
        source_id=source.id,
        source_reputation=0.8,
        published_at=datetime.now(timezone.utc) - timedelta(hours=2),
        language="ru",
        analysis_version="test",
    )
    session.add(article)
    await session.flush()
    session.add(
        ArticleEntity(
            article_id=article.id,
            entity_id=entity_id,
            sentiment=sentiment,
            impact=0.9,
            snippet=f"Фраза про {entity_name}",
            entity_role="primary",
        )
    )


async def test_oil_news_hits_aviation_and_oil_company(session, monkeypatch):
    monkeypatch.setattr("app.market.moex.MOEXClient", lambda: FakeMOEX())
    await seed_graph(session)

    await _store_news(session, "http://test.ru/oil", "Нефть", "positive")
    await session.commit()

    aero_result = await generate_strategy(session, "AFLT")
    assert aero_result["strategy"]["verdict"] == "SELL"
    assert aero_result["strategy_id"] is not None

    lukoil_result = await generate_strategy(session, "LKOH")
    assert lukoil_result["strategy"]["verdict"] == "BUY"


async def test_insufficient_data(session, monkeypatch):
    monkeypatch.setattr("app.market.moex.MOEXClient", lambda: FakeMOEX())
    await seed_graph(session)

    result = await generate_strategy(session, "SBER")
    assert result["strategy"]["verdict"] == "INSUFFICIENT_DATA"


async def test_generate_strategy_without_persist(session, monkeypatch):
    monkeypatch.setattr("app.market.moex.MOEXClient", lambda: FakeMOEX())
    await seed_graph(session)

    await _store_news(session, "http://test.ru/oil3", "Нефть", "positive")
    await session.commit()

    result = await generate_strategy(session, "AFLT", persist=False)
    assert result["strategy"]["verdict"] == "SELL"
    assert "strategy_id" not in result

    strategies = (await session.scalars(select(Strategy))).all()
    assert strategies == []


async def test_collect_news_date_filter(session):
    since = _parse_since(SimpleNamespace(from_date="2026-01-01", days=0))
    assert since == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert _within_since(datetime(2026, 2, 1, tzinfo=timezone.utc), since)
    assert not _within_since(datetime(2025, 12, 31, tzinfo=timezone.utc), since)
    assert _within_since(None, since)
    assert _within_since(datetime(2025, 1, 1, tzinfo=timezone.utc), None)

    by_days = _parse_since(SimpleNamespace(from_date="", days=5))
    assert by_days is not None
    assert by_days > datetime(2025, 1, 1, tzinfo=timezone.utc)

    no_filter = _parse_since(SimpleNamespace(from_date="", days=0))
    assert no_filter is None


async def test_rss_date_parsing():
    assert _parse_date("Tue, 29 Jul 2026 12:34:56 +0300") == datetime(
        2026, 7, 29, 9, 34, 56, tzinfo=timezone.utc
    )
    assert _parse_date("2026-07-29T12:34:56+03:00") == datetime(
        2026, 7, 29, 9, 34, 56, tzinfo=timezone.utc
    )
    assert _parse_date("Wed, 30 Jul 2026 09:00:00 GMT") == datetime(
        2026, 7, 30, 9, 0, 0, tzinfo=timezone.utc
    )
    assert _parse_date("") is None
    assert _parse_date("garbage") is None


async def test_mention_check_restricted_entities(session):
    await seed_graph(session)

    assert await _mention_check(session, "Аэрофлот увеличил пассажиропоток", {"Аэрофлот"})
    assert not await _mention_check(session, "Нефть подорожала", {"Аэрофлот"})
    assert await _mention_check(session, "Нефть подорожала", None)
    assert await _mention_check(session, "Магнит увеличил выручку", {"Магнит"})
    assert not await _mention_check(session, "Землетрясение магнитудой 7,1", {"Магнит"})


async def test_password_hashing():
    hashed = hash_password("secret123")
    assert hashed.startswith("pbkdf2_sha256$")
    assert verify_password("secret123", hashed)
    assert not verify_password("wrong", hashed)


async def test_auth_session_flow(session):
    user = User(username="alice", password_hash=hash_password("secret123"))
    session.add(user)
    await session.flush()

    token = await create_session(session, user)
    record = await session.scalar(select(Session).where(Session.token == token))
    assert record is not None

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 1),
    }
    current = await get_current_user(Request(scope), session)
    assert current.username == "alice"

    await delete_session(session, token)
    record = await session.scalar(select(Session).where(Session.token == token))
    assert record is None


async def test_watchlist_and_position_models(session):
    await seed_graph(session)
    user = User(username="bob", password_hash="x")
    session.add(user)
    await session.flush()
    security = await session.scalar(select(Security).where(Security.ticker == "AFLT"))
    session.add(WatchlistItem(user_id=user.id, security_id=security.id))
    session.add(
        PortfolioPosition(
            user_id=user.id, security_id=security.id, quantity=100, avg_price=50.0
        )
    )
    await session.commit()

    wl = (await session.scalars(select(WatchlistItem))).all()
    assert len(wl) == 1
    positions = (await session.scalars(select(PortfolioPosition))).all()
    assert positions[0].quantity == 100
    assert positions[0].avg_price == 50.0


async def test_alerts_service(session):
    await seed_graph(session)
    user = User(username="alerttest", password_hash="x")
    session.add(user)
    await session.flush()

    security = await session.scalar(select(Security).where(Security.ticker == "AFLT"))
    session.add(WatchlistItem(user_id=user.id, security_id=security.id))

    entity = await session.scalar(select(Entity).where(Entity.name == "Аэрофлот"))
    source = Source(name="Тест-источник", kind="rss", reputation_score=0.8)
    session.add(source)
    await session.flush()
    article = Article(
        title="Аэрофлот объявил о доплатах",
        text="текст",
        url="http://test.ru/aero-alert",
        source_id=source.id,
        source_reputation=0.8,
        published_at=datetime.now(timezone.utc),
        language="ru",
    )
    session.add(article)
    await session.flush()
    session.add(
        ArticleEntity(
            article_id=article.id,
            entity_id=entity.id,
            sentiment="positive",
            impact=0.9,
            snippet="фрагмент",
        )
    )
    await session.commit()

    created = await process_alerts(session, since=None)
    assert len(created) == 1

    alerts = await load_alerts(session, user.id)
    assert len(alerts) == 1
    assert alerts[0].impact == 0.9
    assert alerts[0].is_ambiguous is False

    assert await mark_read(session, user.id, alerts[0].id) is True
    assert await load_alerts(session, user.id, unread_only=True) == []

    settings = await get_settings(session, user.id)
    assert settings.min_impact == 0.7
    updated = await update_settings(
        session, user.id, min_impact=0.5, channels=["telegram"]
    )
    assert updated.min_impact == 0.5
    assert updated.channels == ["telegram"]

    await process_alerts(session, since=None)
    assert len(await load_alerts(session, user.id)) == 1


async def test_macro_service(session):
    await seed_graph(session)
    security = await session.scalar(select(Security).where(Security.ticker == "SBER"))

    wide = MacroEvent(
        event_type="cpi",
        title="CPI РФ",
        event_time=datetime.now(timezone.utc) + timedelta(days=5),
        region="RU",
        expected_impact="high",
        market_wide=True,
        description="",
    )
    session.add(wide)
    await session.flush()

    bound = MacroEvent(
        event_type="earnings_season",
        title="Отчётность Сбербанка",
        event_time=datetime.now(timezone.utc) + timedelta(days=10),
        region="RU",
        expected_impact="high",
        market_wide=False,
        description="",
    )
    session.add(bound)
    await session.flush()
    await session.execute(
        macro_event_security.insert().values(
            event_id=bound.id, security_id=security.id
        )
    )
    await session.commit()

    events = await list_events(session)
    assert len(events) == 2

    titles = [e.title for e, _ in await list_security_events(session, security.id)]
    assert "Отчётность Сбербанка" in titles
    assert "CPI РФ" in titles

    assert await event_tickers(session, bound.id) == ["SBER"]


async def test_macro_page_per_user_filters(session):
    await seed_graph(session)
    sber = await session.scalar(select(Security).where(Security.ticker == "SBER"))
    gazp = await session.scalar(select(Security).where(Security.ticker == "GAZP"))

    user = User(username="alexkwest", password_hash="x")
    session.add(user)
    await session.flush()
    token = await create_session(session, user)
    session.add(WatchlistItem(user_id=user.id, security_id=sber.id))
    session.add(
        PortfolioPosition(user_id=user.id, security_id=gazp.id, quantity=10, avg_price=150.0)
    )

    wide = MacroEvent(
        event_type="cpi",
        title="CPI РФ",
        event_time=datetime.now(timezone.utc) + timedelta(days=1),
        region="RU",
        expected_impact="high",
        market_wide=True,
        description="",
    )
    session.add(wide)
    await session.flush()

    bound = MacroEvent(
        event_type="earnings_season",
        title="Отчётность Сбербанка",
        event_time=datetime.now(timezone.utc) + timedelta(days=2),
        region="RU",
        expected_impact="high",
        market_wide=False,
        description="",
    )
    session.add(bound)
    await session.flush()
    await session.execute(
        macro_event_security.insert().values(event_id=bound.id, security_id=sber.id)
    )
    await session.commit()

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/macro",
            "headers": [(b"cookie", f"nt_token={token}".encode())],
            "server": ("test", 80),
            "query_string": b"",
            "client": ("test", 80),
            "scheme": "http",
        }
    )
    response = await macro_page(request, session)
    html = response.body.decode()

    assert 'new Set(["SBER"])' in html
    assert 'new Set(["GAZP"])' in html
    assert 'data-marketwide="1"' in html
    assert 'data-tickers="SBER"' in html
    assert 'data-tickers="GAZP"' not in html

    anonymous = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/macro",
            "headers": [],
            "server": ("test", 80),
            "query_string": b"",
            "client": ("test", 80),
            "scheme": "http",
        }
    )
    anon_html = (await macro_page(anonymous, session)).body.decode()
    assert "Бумаги из: Watchlist" not in anon_html
    assert "Бумаги из: Портфель" not in anon_html
    assert 'new Set([])' in anon_html


async def test_telegram_link_flow(session):
    code = await create_link_code(session, 123456)
    assert len(code) == 6

    assert await consume_link_code(session, code.lower()) == 123456
    assert await consume_link_code(session, code) is None

    code2 = await create_link_code(session, 789)
    record = await session.scalar(
        select(TelegramLinkCode).where(TelegramLinkCode.code == code2)
    )
    record.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await session.commit()
    assert await consume_link_code(session, code2) is None


async def test_telegram_link_user(session):
    user = User(username="tgbot", password_hash="x")
    session.add(user)
    await session.commit()

    await set_user_chat(session, user.id, 555)
    assert (await session.get(User, user.id)).telegram_chat_id == 555

    await unlink_telegram(session, user.id)
    assert (await session.get(User, user.id)).telegram_chat_id is None


async def test_telegram_delivery(session, monkeypatch):
    await seed_graph(session)
    sber = await session.scalar(select(Security).where(Security.ticker == "SBER"))

    sent = {"calls": []}

    async def fake_send(chat_id: int, text: str) -> None:
        sent["calls"].append((chat_id, text))

    monkeypatch.setattr("app.alerts.delivery.send_message", fake_send)

    linked = User(username="push", password_hash="x", telegram_chat_id=111)
    session.add(linked)
    await session.flush()
    await update_settings(session, linked.id, channels=["telegram"])

    no_chat = User(username="push2", password_hash="x")
    session.add(no_chat)
    await session.flush()
    await update_settings(session, no_chat.id, channels=["telegram"])

    app_only = User(username="push3", password_hash="x", telegram_chat_id=333)
    session.add(app_only)
    await session.flush()
    await update_settings(session, app_only.id, channels=["app"])

    alert1 = Alert(
        user_id=linked.id,
        security_id=sber.id,
        article_id=1,
        headline="Большая новость по SBER",
        url="https://example.com/1",
        impact=0.9,
        is_ambiguous=False,
    )
    alert2 = Alert(
        user_id=no_chat.id,
        security_id=sber.id,
        article_id=2,
        headline="Без чата",
        url="https://example.com/2",
        impact=0.9,
        is_ambiguous=False,
    )
    alert3 = Alert(
        user_id=app_only.id,
        security_id=sber.id,
        article_id=3,
        headline="Только веб",
        url="https://example.com/3",
        impact=0.9,
        is_ambiguous=False,
    )
    session.add_all([alert1, alert2, alert3])
    await session.commit()

    count = await deliver_telegram(session, [alert1, alert2, alert3])
    assert count == 1
    assert sent["calls"][0][0] == 111
    assert "Большая новость по SBER" in sent["calls"][0][1]
    assert "SBER" in sent["calls"][0][1]
