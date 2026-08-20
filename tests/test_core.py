from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.alerts.service import (
    get_settings,
    load_alerts,
    mark_read,
    process_alerts,
    unread_count,
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
    EvidenceItem,
    MacroEvent,
    MarketCandle,
    PortfolioPosition,
    Security,
    Session,
    Source,
    Strategy,
    ScriptRun,
    TelegramLinkCode,
    User,
    UserFeedback,
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
from app.feedback.service import (
    get_rating,
    ratings_map,
    record_feedback_for_security,
    set_feedback,
    user_stats,
)
from scripts.collect_news import _parse_since, collect_telegram_news
from app.news.ingest import (
    ensure_sources,
    filter_candidates,
    ingest_candidates,
    mention_check as _mention_check,
    within_since as _within_since,
)
from app.collectors.rss import RawArticle
from app.collectors.telegram import _make_title
from scripts.backtest_asof import backtest_ticker, build_report, evaluate
from app.admin.roles import promote_admin_users
from app.admin.runner import build_argv, get_script, mark_stale_runs
from app.web.router import admin_page as admin_page_route
from app.paper.service import (
    account_view,
    get_or_create_account,
    process_signals,
    reset_account,
)
from app.strategy.weights import calibrate, create_version, get_latest
from app.notices.service import add_notice, notice_state
from app.notices.monitor import friendly_error, is_fresh, set_source_notice
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


async def test_research_source_ref_in_path_and_evidence(session, monkeypatch):
    """Цепочки с не-empty source_ref переносят ссылку и создают research-обоснование."""
    from app.db.models import EvidenceItem, Influence

    monkeypatch.setattr("app.market.moex.MOEXClient", lambda: FakeMOEX())
    await seed_graph(session)

    # Добавляем курируемую связь с реальной ссылкой обоснования (FR-05-08):
    # берём уже посеянную «Нефть → Нефтегазовый сектор» и проставляем ссылку.
    oil_id = await resolve_entity_id(session, "Нефть")
    sector_id = await resolve_entity_id(session, "Нефтегазовый сектор")
    edge = (
        await session.scalars(
            select(Influence).where(
                Influence.from_entity_id == oil_id,
                Influence.to_entity_id == sector_id,
            )
        )
    ).first()
    edge.source_ref = "https://example.com/oil-gaz-research"
    await session.flush()

    # Существующая связь тоже оставляем без изменений; путь до сектора теперь
    # проходит через связь со ссылкой.
    lukoil_id = await resolve_entity_id(session, "Лукойл")
    paths = await find_influence_paths(session, oil_id, lukoil_id)
    assert paths
    assert paths[0].source_ref == "https://example.com/oil-gaz-research"

    # Генерация стратегии по Лукойлу (новая нефтяная новость) — должен
    # появиться research-элемент обоснования в БД.
    await _store_news(session, "http://test.ru/oil-research", "Нефть", "positive")
    await session.commit()

    result = await generate_strategy(session, "LKOH")
    assert result["strategy_id"] is not None
    assert "https://example.com/oil-gaz-research" in result["research"]

    ev = (
        await session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.strategy_id == result["strategy_id"],
                EvidenceItem.kind == "research",
            )
        )
    ).all()
    assert len(ev) >= 1
    assert ev[0].url == "https://example.com/oil-gaz-research"


async def test_insufficient_data(session, monkeypatch):
    monkeypatch.setattr("app.market.moex.MOEXClient", lambda: FakeMOEX())
    await seed_graph(session)

    result = await generate_strategy(session, "SBER")
    assert result["strategy"]["verdict"] == "INSUFFICIENT_DATA"


async def test_add_influence_with_source(session):
    """Сервис добавления ребра/ссылки: создание, дополнение без дублей, новый entity."""
    from app.graph.service import add_influence_with_source
    from app.db.models import Influence

    await seed_graph(session)

    # Новое ребро «Сталь → Электрогенерация» с недостающей сущностью
    res = await add_influence_with_source(
        session,
        from_name="Сталь",
        to_name="Электрогенерация",
        url="https://example.com/steel-power",
        direction="negative",
    )
    assert res["status"] == "created"
    await session.commit()

    # Повторное добавление той же ссылки — дубликат
    res2 = await add_influence_with_source(
        session, from_name="Сталь", to_name="Электрогенерация",
        url="https://example.com/steel-power", direction="negative",
    )
    assert res2["status"] == "duplicate"
    await session.commit()

    # Дополнение существующего ребра новой ссылкой
    res3 = await add_influence_with_source(
        session, from_name="Сталь", to_name="Электрогенерация",
        url="https://example.com/steel-power-2", direction="negative",
    )
    assert res3["status"] == "updated"
    await session.commit()

    inf = await session.get(Influence, res["influence_id"])
    assert inf.source_ref == (
        "https://example.com/steel-power,https://example.com/steel-power-2"
    )
    # Сущность создана
    from app.graph.service import resolve_entity_id
    assert await resolve_entity_id(session, "Электрогенерация") is not None


async def test_pdf_relation_parse_and_analyze(session, monkeypatch):
    """Парсинг LLM-результата и анализ PDF (мок текста и LLM-клиента)."""
    from app.graph.pdf_analysis import (
        analyze_pdf_relation,
        parse_relation,
    )

    # Чистый парсинг
    rel = parse_relation(
        '{"from_name": "Нефть", "to_name": "Нефтегазовый сектор", '
        '"direction": "positive", "rationale": "короткое обоснование", "confidence": 0.9}'
    )
    assert rel.is_valid
    assert rel.from_name == "Нефть"
    assert rel.to_name == "Нефтегазовый сектор"
    assert rel.direction == "positive"

    # analyze_pdf_relation с мок-клиентом и моком извлечения текста
    monkeypatch.setattr(
        "app.graph.pdf_analysis.extract_pdf_text", lambda *a, **k: "Цены на нефть влияют на нефтегазовый сектор."
    )
    expected = '{"from_name": "Нефть", "to_name": "Нефтегазовый сектор", "confidence": 0.85}'
    import asyncio

    class FakeClient:
        async def chat(self, system_prompt, user_prompt):
            return expected

    result = await analyze_pdf_relation("whatever.pdf", client=FakeClient())
    assert result.is_valid
    assert result.from_name == "Нефть"
    assert result.to_name == "Нефтегазовый сектор"


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


async def test_generate_strategy_tolerates_moex_failure(session, monkeypatch):
    """Движок строит стратегию без 500, когда MOEX недоступен (use_live_market)."""

    class BrokenMOEX:
        async def fetch_quote(self, ticker: str):
            raise httpx.ConnectError("MOEX недоступен")

        async def fetch_daily_closes(self, ticker: str, days: int = 60):
            raise httpx.ConnectError("MOEX недоступен")

    monkeypatch.setattr("app.market.moex.MOEXClient", lambda: BrokenMOEX())
    await seed_graph(session)
    await _store_news(session, "http://test.ru/oil4", "Нефть", "positive")
    await session.commit()

    result = await generate_strategy(session, "AFLT", persist=False, use_live_market=True)
    assert result["strategy"]["verdict"] in ("BUY", "SELL", "HOLD")


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
    assert await unread_count(session, user.id) == 1

    assert await mark_read(session, user.id, alerts[0].id) is True
    assert await load_alerts(session, user.id, unread_only=True) == []
    assert await unread_count(session, user.id) == 0

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


async def test_feedback_flow(session):
    await seed_graph(session)
    sber = await session.scalar(select(Security).where(Security.ticker == "SBER"))
    user = User(username="fbuser", password_hash="x")
    session.add(user)
    await session.commit()

    strategy = Strategy(security_id=sber.id, verdict="BUY", horizon="medium")
    session.add(strategy)
    await session.commit()

    feedback = await set_feedback(session, strategy.id, user.id, "worked")
    assert feedback.rating == "worked"
    assert await get_rating(session, strategy.id, user.id) == "worked"
    assert await ratings_map(session, [strategy.id], user.id) == {strategy.id: "worked"}

    await set_feedback(session, strategy.id, user.id, "failed", "не угадали")
    rows = (
        await session.scalars(
            select(UserFeedback).where(UserFeedback.strategy_id == strategy.id)
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].rating == "failed"
    assert rows[0].comment == "не угадали"

    try:
        await set_feedback(session, strategy.id, user.id, "bogus")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid rating must raise ValueError")

    try:
        await set_feedback(session, 999999, user.id, "worked")
    except KeyError:
        pass
    else:
        raise AssertionError("missing strategy must raise KeyError")

    stats = await user_stats(session, user.id)
    assert stats["failed"] == 1
    assert stats["total"] == 1
    assert stats["worked_percent"] == 0.0


async def test_feedback_neutral_and_security_record(session):
    await seed_graph(session)
    sber = await session.scalar(select(Security).where(Security.ticker == "SBER"))
    user = User(username="closer", password_hash="x")
    session.add(user)
    await session.commit()

    strategy = Strategy(security_id=sber.id, verdict="BUY", horizon="short")
    session.add(strategy)
    await session.commit()

    feedback = await set_feedback(session, strategy.id, user.id, "neutral")
    assert feedback.rating == "neutral"

    stats = await user_stats(session, user.id)
    assert stats["neutral"] == 1
    assert stats["total"] == 1

    assert await record_feedback_for_security(session, sber.id, user.id, "worked") is True
    assert await get_rating(session, strategy.id, user.id) == "worked"

    no_strategy = await session.scalar(
        select(Security).where(Security.ticker == "OZON")
    )
    assert (
        await record_feedback_for_security(session, no_strategy.id, user.id, "worked")
        is False
    )


def test_telegram_title_helper():
    assert _make_title("Первое предложение. Второе.") == "Первое предложение."
    long = "Текст" * 40
    assert len(_make_title(long)) == 80


async def test_ingest_telegram_article(session):
    await seed_graph(session)
    sources = [
        {
            "name": "Маркет-канал",
            "kind": "telegram",
            "reputation": 0.6,
            "config": {"channel": "market_channel"},
        }
    ]
    source_ids, reputation = await ensure_sources(session, sources)
    assert "Маркет-канал" in source_ids

    entity = SimpleNamespace(
        name="Аэрофлот",
        sentiment="positive",
        impact=0.8,
        snippet="фрагмент",
        role="primary",
    )
    analyzer = SimpleNamespace(
        analyze=AsyncMock(
            return_value=SimpleNamespace(
                is_reliable=True, topic="company", entities=[entity]
            )
        )
    )
    raw = RawArticle(
        title="Аэрофлот отчитался",
        text="Аэрофлот увеличил пассажиропоток",
        url="https://t.me/market_channel/12",
        source_name="Маркет-канал",
        published_at=datetime.now(timezone.utc),
    )
    stored = await ingest_candidates(
        session, [raw], source_ids, reputation, analyzer, "llm:test"
    )
    assert stored == 1

    article = await session.scalar(
        select(Article).where(Article.url == "https://t.me/market_channel/12")
    )
    assert article is not None
    assert article.source_id == source_ids["Маркет-канал"]

    stored_again = await ingest_candidates(
        session, [raw], source_ids, reputation, analyzer, "llm:test"
    )
    assert stored_again == 0


async def test_filter_candidates_mentions(session):
    await seed_graph(session)
    known = {"https://t.me/market_channel/1"}
    raw = [
        RawArticle(
            title="Аэрофлот отчитался",
            text="Аэрофлот увеличил прибыль",
            url="https://t.me/market_channel/2",
            source_name="chan",
            published_at=datetime.now(timezone.utc),
        ),
        RawArticle(
            title="Ни о чём",
            text="Ничего важного",
            url="https://t.me/market_channel/3",
            source_name="chan",
            published_at=datetime.now(timezone.utc),
        ),
    ]
    candidates = await filter_candidates(session, raw, None, {"Аэрофлот"}, known)
    assert [c.url for c in candidates] == ["https://t.me/market_channel/2"]


async def test_collect_telegram_disabled(session, monkeypatch):
    fake = SimpleNamespace(
        telegram_api_id="", telegram_api_hash="", telegram_channel_list=[]
    )
    monkeypatch.setattr("scripts.collect_news.get_settings", lambda: fake)
    assert await collect_telegram_news(session) == 0


async def test_generate_strategy_no_lookahead(session):
    await seed_graph(session)
    aero_id = await resolve_entity_id(session, "Аэрофлот")
    source = Source(name="Тест-агентство", kind="rss", reputation_score=0.8)
    session.add(source)
    await session.flush()

    as_of = datetime(2026, 5, 10, 18, 0, tzinfo=timezone.utc)

    future = Article(
        title="Аэрофлот после T",
        text="Аэрофлот растёт",
        url="https://t.me/future/1",
        source_id=source.id,
        source_reputation=0.8,
        published_at=as_of + timedelta(days=1),
        language="ru",
    )
    session.add(future)
    await session.flush()
    session.add(
        ArticleEntity(
            article_id=future.id,
            entity_id=aero_id,
            sentiment="positive",
            impact=1.0,
            snippet="",
            entity_role="primary",
        )
    )
    await session.commit()

    result = await generate_strategy(
        session, "AFLT", as_of=as_of, persist=False, use_live_market=False
    )
    assert result["strategy"]["verdict"] == "INSUFFICIENT_DATA"

    past = Article(
        title="Аэрофлот до T",
        text="Аэрофлот укрепляет позиции",
        url="https://t.me/past/1",
        source_id=source.id,
        source_reputation=0.8,
        published_at=as_of - timedelta(hours=2),
        language="ru",
    )
    session.add(past)
    await session.flush()
    session.add(
        ArticleEntity(
            article_id=past.id,
            entity_id=aero_id,
            sentiment="positive",
            impact=1.0,
            snippet="",
            entity_role="primary",
        )
    )
    await session.commit()

    result2 = await generate_strategy(
        session, "AFLT", as_of=as_of, persist=False, use_live_market=False
    )
    assert result2["strategy"]["verdict"] == "BUY"


async def test_generate_strategy_counterarguments(session):
    await seed_graph(session)
    aero_id = await resolve_entity_id(session, "Аэрофлот")
    source = Source(name="Тест-агентство", kind="rss", reputation_score=0.8)
    session.add(source)
    await session.flush()
    as_of = datetime(2026, 5, 10, 18, 0, tzinfo=timezone.utc)

    pos = Article(
        title="Аэрофлот растёт",
        text="Аэрофлот увеличивает перевозки",
        url="https://t.me/pos/1",
        source_id=source.id,
        source_reputation=0.8,
        published_at=as_of - timedelta(hours=2),
        language="ru",
    )
    neg = Article(
        title="Аэрофлот под давлением",
        text="Аэрофлот теряет долю рынка",
        url="https://t.me/neg/1",
        source_id=source.id,
        source_reputation=0.8,
        published_at=as_of - timedelta(hours=1),
        language="ru",
    )
    session.add_all([pos, neg])
    await session.flush()
    session.add(
        ArticleEntity(
            article_id=pos.id, entity_id=aero_id, sentiment="positive",
            impact=1.0, snippet="", entity_role="primary",
        )
    )
    session.add(
        ArticleEntity(
            article_id=neg.id, entity_id=aero_id, sentiment="negative",
            impact=0.5, snippet="", entity_role="primary",
        )
    )
    await session.commit()

    result = await generate_strategy(
        session, "AFLT", as_of=as_of, persist=False, use_live_market=False
    )
    assert result["strategy"]["verdict"] == "BUY"
    assert result["counterarguments"]
    assert any(
        "Аэрофлот" in ca["text"] and "ослабляет" in ca["text"]
        for ca in result["counterarguments"]
    )
    assert result["risks"]
    assert any("отраслевой" in r for r in result["risks"])

    persisted = await generate_strategy(
        session, "AFLT", as_of=as_of, persist=True, use_live_market=False
    )
    items = (
        await session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.strategy_id == persisted["strategy_id"],
                EvidenceItem.kind == "counterargument",
            )
        )
    ).all()
    assert items


def test_backtest_evaluate():
    ret, correct = evaluate("BUY", 100.0, 105.0)
    assert abs(ret - 0.05) < 1e-9 and correct is True
    ret, correct = evaluate("BUY", 100.0, 95.0)
    assert abs(ret + 0.05) < 1e-9 and correct is False
    ret, correct = evaluate("SELL", 100.0, 95.0)
    assert abs(ret + 0.05) < 1e-9 and correct is True
    ret, correct = evaluate("HOLD", 100.0, 105.0)
    assert abs(ret - 0.05) < 1e-9 and correct is None
    assert evaluate("BUY", None, 105.0) == (None, None)


async def test_backtest_asof_ticker(session):
    await seed_graph(session)
    aflt = await session.scalar(select(Security).where(Security.ticker == "AFLT"))
    source = Source(name="Тест-агентство", kind="rss", reputation_score=0.8)
    session.add(source)
    await session.flush()
    aero_id = await resolve_entity_id(session, "Аэрофлот")
    article = Article(
        title="Аэрофлот позитив",
        text="Аэрофлот увеличивает перевозки",
        url="https://t.me/bt/1",
        source_id=source.id,
        source_reputation=0.8,
        published_at=datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc),
        language="ru",
    )
    session.add(article)
    await session.flush()
    session.add(
        ArticleEntity(
            article_id=article.id,
            entity_id=aero_id,
            sentiment="positive",
            impact=5.0,
            snippet="",
            entity_role="primary",
        )
    )
    for i in range(12):
        session.add(
            MarketCandle(
                security_id=aflt.id,
                trading_date=date(2026, 1, 3 + i),
                close=100.0 + i,
            )
        )
    await session.commit()

    results = await backtest_ticker(
        session, "AFLT", date(2026, 1, 3), date(2026, 1, 10), horizon=2, step_days=1
    )
    assert results
    assert all(r.verdict == "BUY" for r in results)
    assert all(r.correct is True for r in results)
    assert all(r.forward_return is not None and r.forward_return > 0 for r in results)

    report = build_report(results, 2)
    joined = "\n".join(report)
    assert "Оценено:" in joined
    assert "По вердиктам:" in joined
    assert "По периодам (месяц):" in joined


async def test_promote_admin_users(session):
    user = User(username="alexkwest", password_hash="x")
    plain = User(username="plain", password_hash="x")
    session.add_all([user, plain])
    await session.commit()

    promoted = await promote_admin_users(session, ["alexkwest"])
    assert promoted == 1
    assert (await session.get(User, user.id)).role == "admin"
    assert (await session.get(User, plain.id)).role == "user"

    assert await promote_admin_users(session, ["alexkwest"]) == 0


def test_runner_build_argv():
    argv = build_argv("update_prices", None)
    assert argv[-2:] == ["--days", "5"]
    argv = build_argv("collect_news", {"--days": 3})
    assert argv[-2:] == ["--days", "3"]

    assert build_argv("seed_db", None) == [
        __import__("sys").executable,
        "-u",
        "-m",
        "scripts.seed_db",
    ]

    try:
        build_argv("nope", None)
    except ValueError:
        pass
    else:
        raise AssertionError("unknown script must raise ValueError")

    try:
        build_argv("collect_news", {"--days": -1})
    except ValueError:
        pass
    else:
        raise AssertionError("non-positive param must raise ValueError")

    assert get_script("backtest_asof") is not None


def test_script_run_timezone_columns():
    from sqlalchemy import DateTime

    table = ScriptRun.__table__
    for name in ("started_at", "finished_at"):
        col = table.c[name]
        assert isinstance(col.type, DateTime), f"{name} must be DateTime"
        assert col.type.timezone is True, f"{name} must be timezone-aware"


async def test_mark_stale_runs(session):
    run = ScriptRun(script_name="backtest_asof", params={}, user_id=None, status="running")
    done = ScriptRun(
        script_name="seed_db", params={}, user_id=None, status="success",
        exit_code=0, output="ok",
    )
    session.add_all([run, done])
    await session.commit()

    fixed = await mark_stale_runs(session)
    assert fixed == 1

    run = await session.get(ScriptRun, run.id)
    assert run.status == "failed"
    assert run.exit_code == -1
    assert run.finished_at is not None
    assert "прервано" in run.output

    done = await session.get(ScriptRun, done.id)
    assert done.status == "success"
    assert done.exit_code == 0


async def test_admin_page_access(session):
    await seed_graph(session)
    admin = User(username="boss", password_hash="x", role="admin")
    plain = User(username="worker", password_hash="x")
    session.add_all([admin, plain])
    await session.flush()
    admin_token = await create_session(session, admin)
    plain_token = await create_session(session, plain)
    await session.commit()

    def make_request(token: str | None) -> Request:
        headers = [(b"cookie", f"nt_token={token}".encode())] if token else []
        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/admin",
                "headers": headers,
                "server": ("test", 80),
                "query_string": b"",
                "client": ("test", 80),
                "scheme": "http",
            }
        )

    admin_html = (await admin_page_route(make_request(admin_token), session)).body.decode()
    assert "Администрирование" in admin_html
    assert "Ежедневный конвейер" in admin_html
    assert "Последние запуски" in admin_html

    with pytest.raises(HTTPException) as excinfo:
        await admin_page_route(make_request(plain_token), session)
    assert excinfo.value.status_code == 403

    anon_response = await admin_page_route(make_request(None), session)
    assert anon_response.status_code == 303


async def test_portfolio_page_tolerates_moex_failure(session, monkeypatch):
    """/portfolio рендерится без 500, когда MOEX недоступен (fetch_quote падает)."""
    from app.web.router import portfolio_page

    await seed_graph(session)
    sber = await session.scalar(select(Security).where(Security.ticker == "SBER"))
    user = User(username="portfolioowner", password_hash="x")
    session.add(user)
    await session.flush()
    session.add(PortfolioPosition(user_id=user.id, security_id=sber.id, quantity=10, avg_price=100))
    token = await create_session(session, user)
    await session.commit()

    async def _boom(ticker: str):
        raise httpx.ConnectError("MOEX недоступен")

    monkeypatch.setattr("app.web.router._moex.fetch_quote", _boom)

    headers = [(b"cookie", f"nt_token={token}".encode())]
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/portfolio",
            "headers": headers,
            "server": ("test", 80),
            "query_string": b"",
            "client": ("test", 80),
            "scheme": "http",
        }
    )
    response = await portfolio_page(request, session)
    assert response.status_code == 200
    body = response.body.decode()
    assert "SBER" in body


async def test_paper_signals_open_and_close(session):
    await seed_graph(session)
    aflt = await session.scalar(select(Security).where(Security.ticker == "AFLT"))
    user = User(username="papertrader", password_hash="x")
    session.add(user)
    await session.commit()

    for i in range(10):
        session.add(
            MarketCandle(
                security_id=aflt.id,
                trading_date=date(2026, 1, 1 + i),
                close=100.0 + i,
            )
        )
    await session.commit()

    account = await get_or_create_account(session, user.id)
    assert account.initial_capital == 1_000_000.0
    account.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await session.commit()

    buy = Strategy(
        security_id=aflt.id,
        verdict="BUY",
        generated_at=datetime(2026, 1, 5, 10, 0),
        model_version="mvp-0.1",
    )
    session.add(buy)
    await session.commit()

    result = await process_signals(session, account)
    assert result == {"opened": 1, "closed": 0}

    view = await account_view(session, account)
    assert len(view["positions"]) == 1
    position = view["positions"][0]
    assert position["ticker"] == "AFLT"
    assert position["entry_price"] == 109.0

    session.add(
        MarketCandle(
            security_id=aflt.id,
            trading_date=date(2026, 1, 11),
            close=130.0,
        )
    )
    sell = Strategy(
        security_id=aflt.id,
        verdict="SELL",
        generated_at=datetime(2026, 1, 8, 10, 0),
        model_version="mvp-0.1",
    )
    session.add(sell)
    await session.commit()

    result2 = await process_signals(session, account)
    assert result2 == {"opened": 0, "closed": 1}

    view2 = await account_view(session, account)
    assert view2["positions"] == []
    assert view2["metrics"]["total_closed"] == 1
    assert view2["metrics"]["wins"] == 1
    assert view2["metrics"]["realized"] > 0
    assert view2["metrics"]["benchmark_return"] is not None
    assert any(t["side"] == "close" for t in view2["trades"])

    await reset_account(session, account)
    view3 = await account_view(session, account)
    assert view3["positions"] == []
    assert view3["trades"] == []
    assert view3["metrics"]["total_closed"] == 0


async def test_paper_signals_no_fill_before_signal(session):
    await seed_graph(session)
    aflt = await session.scalar(select(Security).where(Security.ticker == "AFLT"))
    user = User(username="paper2", password_hash="x")
    session.add(user)
    await session.commit()

    session.add(
        MarketCandle(
            security_id=aflt.id,
            trading_date=date(2026, 2, 1),
            close=100.0,
        )
    )
    await session.commit()

    account = await get_or_create_account(session, user.id)
    future_strategy = Strategy(
        security_id=aflt.id,
        verdict="BUY",
        generated_at=datetime(2026, 2, 5, 10, 0),
        model_version="mvp-0.1",
    )
    session.add(future_strategy)
    await session.commit()

    result = await process_signals(session, account)
    assert result == {"opened": 0, "closed": 0}
    view = await account_view(session, account)
    assert view["positions"] == []


async def test_weights_apply_to_engine(session):
    await seed_graph(session)
    aero_id = await resolve_entity_id(session, "Аэрофлот")
    source = Source(name="Тест-агентство", kind="rss", reputation_score=0.8)
    session.add(source)
    await session.flush()
    as_of = datetime(2026, 5, 10, 18, 0, tzinfo=timezone.utc)
    article = Article(
        title="Аэрофлот растёт",
        text="Аэрофлот увеличивает перевозки",
        url="https://t.me/w/1",
        source_id=source.id,
        source_reputation=0.8,
        published_at=as_of - timedelta(hours=2),
        language="ru",
    )
    session.add(article)
    await session.flush()
    session.add(
        ArticleEntity(
            article_id=article.id, entity_id=aero_id, sentiment="positive",
            impact=1.0, snippet="", entity_role="primary",
        )
    )
    await session.commit()

    version, factors = await get_latest(session)
    assert version is None
    assert factors == {"news": 1.0, "graph": 1.0, "counter_penalty": 1.0}

    result = await generate_strategy(
        session, "AFLT", as_of=as_of, persist=False, use_live_market=False
    )
    assert result["strategy"]["verdict"] == "BUY"
    assert result["weights_version"] is None

    await create_version(session, {"news": 0.1, "graph": 1.0, "counter_penalty": 1.0})
    result2 = await generate_strategy(
        session, "AFLT", as_of=as_of, persist=False, use_live_market=False
    )
    assert result2["weights_version"] == "w1"
    assert result2["strategy"]["verdict"] == "HOLD"


async def test_calibrate_weights_from_feedback(session):
    await seed_graph(session)
    sber = await session.scalar(select(Security).where(Security.ticker == "SBER"))
    user = User(username="wuser", password_hash="x")
    session.add(user)
    await session.commit()

    worked = Strategy(security_id=sber.id, verdict="BUY", model_version="mvp-0.1")
    failed = Strategy(security_id=sber.id, verdict="SELL", model_version="mvp-0.1")
    session.add_all([worked, failed])
    await session.flush()
    session.add_all(
        [
            EvidenceItem(strategy_id=worked.id, kind="news_fact", quote="n", weight=1.0),
            EvidenceItem(strategy_id=worked.id, kind="graph_path", quote="g", weight=0.1),
            EvidenceItem(strategy_id=failed.id, kind="news_fact", quote="n", weight=0.1),
            EvidenceItem(strategy_id=failed.id, kind="graph_path", quote="g", weight=1.0),
            UserFeedback(strategy_id=worked.id, user_id=user.id, rating="worked"),
            UserFeedback(strategy_id=failed.id, user_id=user.id, rating="failed"),
        ]
    )
    await session.commit()

    report = await calibrate(session)
    assert report["version"] == "w1"
    assert report["worked_count"] == 1
    assert report["failed_count"] == 1
    assert report["changes"]
    assert report["factors"]["graph"] < 1.0
    assert report["factors"]["news"] > 1.0

    version, factors = await get_latest(session)
    assert version == "w1"


async def test_notices_states(session):
    state = await notice_state(session)
    assert state["state"] == "none"
    assert state["notices"] == []

    await add_notice(session, "info", "Давно не запускался конвейер", source="stale_daily_pipeline")
    state = await notice_state(session)
    assert state["state"] == "info"
    assert len(state["notices"]) == 1
    assert state["notices"][0]["level"] == "info"
    assert state["notices"][0]["source"] == "stale_daily_pipeline"

    await add_notice(session, "warning", "Нет подключения к Telegram-боту", source="telegram")
    state = await notice_state(session)
    assert state["state"] == "warning"

    await add_notice(session, "critical", "Скрипт упал", source="script_run")
    state = await notice_state(session)
    assert state["state"] == "critical"
    assert state["notices"][0]["level"] == "critical"


def test_is_fresh():
    today = date(2026, 5, 20)
    assert is_fresh(None, 3, today) is True
    assert is_fresh(date(2026, 5, 18), 3, today) is True
    assert is_fresh(date(2026, 5, 10), 3, today) is False
    assert is_fresh(datetime(2026, 5, 18, 10, 0), 3, today) is True


def test_friendly_error():
    assert (
        friendly_error(TimeoutError("x"), "Telegram-бот недоступен")
        == "Telegram-бот недоступен: таймаут соединения"
    )
    assert (
        friendly_error(ConnectionError("x"), "MOEX ISS недоступен")
        == "MOEX ISS недоступен: нет соединения"
    )
    assert friendly_error(RuntimeError("boom"), "Компонент") == "Компонент: RuntimeError: boom"


def test_is_db_error():
    from sqlalchemy.exc import OperationalError

    from app.web.middleware import is_db_error

    assert is_db_error(ConnectionError("refused")) is True
    assert is_db_error(OperationalError("SELECT 1", {}, Exception("connect"))) is True
    assert is_db_error(ValueError("boom")) is False


async def test_set_source_notice_lifecycle(session):
    await set_source_notice(
        session, "llm", "critical", "Нет подключения к ИИ-анализу (LLM): timeout", active=True
    )
    state = await notice_state(session)
    assert state["state"] == "critical"
    assert len(state["notices"]) == 1
    assert state["notices"][0]["source"] == "llm"

    await set_source_notice(session, "llm", "critical", "Обновлённое сообщение", active=True)
    state = await notice_state(session)
    assert len(state["notices"]) == 1
    assert state["notices"][0]["text"] == "Обновлённое сообщение"

    await set_source_notice(session, "llm", "critical", "", active=False)
    state = await notice_state(session)
    assert state["state"] == "none"
    assert state["notices"] == []


async def test_notices_warning_and_critical(session):
    await set_source_notice(session, "telegram", "warning", "Нет подключения к Telegram-боту", active=True)
    state = await notice_state(session)
    assert state["state"] == "warning"

    await set_source_notice(session, "llm", "critical", "Нет подключения к LLM", active=True)
    state = await notice_state(session)
    assert state["state"] == "critical"


async def test_notice_state_worst_severity(session):
    await set_source_notice(session, "telegram", "warning", "нет соединения", active=True)
    assert (await notice_state(session))["state"] == "warning"

    await set_source_notice(session, "stale_prices", "info", "устарело", active=True)
    assert (await notice_state(session))["state"] == "warning"

    await set_source_notice(session, "llm", "critical", "нет LLM", active=True)
    assert (await notice_state(session))["state"] == "critical"

    await set_source_notice(session, "llm", "critical", "", active=False)
    assert (await notice_state(session))["state"] == "warning"

    await set_source_notice(session, "telegram", "warning", "", active=False)
    assert (await notice_state(session))["state"] == "info"

    await set_source_notice(session, "stale_prices", "info", "", active=False)
    assert (await notice_state(session))["state"] == "none"


async def test_futures_templates_page_access(session):
    """Страница шаблонов фьючерсов доступна только admin; сохранение создаёт шаблон."""
    from app.web.router import (
        admin_futures_templates_page,
        admin_futures_templates_save,
    )
    from app.db.models import FuturesTemplate

    admin = User(username="boss2", password_hash="x", role="admin")
    plain = User(username="worker2", password_hash="x")
    session.add_all([admin, plain])
    await session.flush()
    admin_token = await create_session(session, admin)
    plain_token = await create_session(session, plain)
    await session.commit()

    def make_request(token: str | None, method: str = "GET") -> Request:
        headers = [(b"cookie", f"nt_token={token}".encode())] if token else []
        return Request(
            {
                "type": "http",
                "method": method,
                "path": "/admin/futures-templates",
                "headers": headers,
                "server": ("test", 80),
                "query_string": b"",
                "client": ("test", 80),
                "scheme": "http",
            }
        )

    html = (await admin_futures_templates_page(make_request(admin_token), session)).body.decode()
    assert "Шаблоны фьючерсов" in html
    assert "Загрузить фьючерсы" in html

    with pytest.raises(HTTPException) as excinfo:
        await admin_futures_templates_page(make_request(plain_token), session)
    assert excinfo.value.status_code == 403

    # сохранение: новый шаблон
    form = {
        "type": "http",
        "method": "POST",
        "path": "/admin/futures-templates/save",
        "headers": [(b"cookie", f"nt_token={admin_token}".encode())],
        "server": ("test", 80),
        "query_string": b"",
        "client": ("test", 80),
        "scheme": "http",
    }
    req = Request(form)
    req._form = {"id": "", "name": "tpl_page", "tickers": "W4V6, AFU6"}
    resp = await admin_futures_templates_save(req, session)
    assert resp.status_code == 303
    tpl = await session.scalar(
        select(FuturesTemplate).where(FuturesTemplate.name == "tpl_page")
    )
    assert tpl is not None and tpl.tickers == "W4V6,AFU6"
    await session.delete(tpl)
    await session.commit()


async def test_admin_oi_template_skips_ticker(session, monkeypatch):
    """При выборе шаблона фьючерсов поле --ticker не передаётся в запуск OI."""
    from app.web.router import admin_run_script
    from app.admin import runner as runner_mod
    from app.db.models import FuturesTemplate

    admin = User(username="boss3", password_hash="x", role="admin")
    session.add(admin)
    await session.flush()
    token = await create_session(session, admin)
    tpl = FuturesTemplate(name="tpl_oi", tickers="AFU6,SRU6")
    session.add(tpl)
    await session.commit()

    started = {}

    def fake_launch(run_id, script_key, param_values):
        started["params"] = param_values

    monkeypatch.setattr("app.web.router.launch", fake_launch)

    form = {
        "type": "http",
        "method": "POST",
        "path": "/admin/scripts/run",
        "headers": [(b"cookie", f"nt_token={token}".encode())],
        "server": ("test", 80),
        "query_string": b"",
        "client": ("test", 80),
        "scheme": "http",
    }
    req = Request(form)
    # шаблон выбран, поле тикера disabled (не передаётся формой)
    req._form = {"script": "update_oi", "param_tickers": str(tpl.id), "param_days": "5"}
    resp = await admin_run_script(req, session)
    assert resp.status_code == 303
    assert started["params"]["--tickers"] == "AFU6,SRU6"
    assert started["params"]["--ticker"] == ""


async def test_admin_pipeline_injects_saved_template_for_phase2(session, monkeypatch):
    """Ежедневный конвейер получает --tickers из шаблона, сохранённого пользователем."""
    from app.web.router import admin_run_script
    from app.db.models import FuturesTemplate, UserPipelinePref

    admin = User(username="boss4", password_hash="x", role="admin")
    session.add(admin)
    await session.flush()
    token = await create_session(session, admin)
    tpl = FuturesTemplate(name="tpl_pipe", tickers="W4V6,SRZ6")
    session.add(tpl)
    await session.commit()
    session.add(
        UserPipelinePref(user_id=admin.id, last_futures_template_id=tpl.id)
    )
    await session.commit()

    started = {}

    def fake_launch(run_id, script_key, param_values):
        started["params"] = param_values

    monkeypatch.setattr("app.web.router.launch", fake_launch)

    form = {
        "type": "http",
        "method": "POST",
        "path": "/admin/scripts/run",
        "headers": [(b"cookie", f"nt_token={token}".encode())],
        "server": ("test", 80),
        "query_string": b"",
        "client": ("test", 80),
        "scheme": "http",
    }
    req = Request(form)
    # Запуск конвейера с фазы 1 — фаза 2 (фьючерсы) будет выполняться
    req._form = {"script": "daily_pipeline", "param_from-phase": "1"}
    resp = await admin_run_script(req, session)
    assert resp.status_code == 303
    assert started["params"]["--tickers"] == "W4V6,SRZ6"

    # Запуск с фазы 3 — фаза 2 не выполняется, шаблон не подставляется
    req._form = {"script": "daily_pipeline", "param_from-phase": "3"}
    resp = await admin_run_script(req, session)
    assert resp.status_code == 303
    assert started["params"].get("--tickers") in (None, "")


def test_pipeline_phases_and_failed_phase():
    """Состояния фаз конвейера (done/error/running/skipped) и фаза сбоя из лога."""
    from app.admin.runner import _pipeline_failed_phase
    from app.web.router import _pipeline_phases

    out = (
        "Пропускаю фазы 1..2 (запуск с фазы 3/5)\n"
        "Фаза 3/5: генерация стратегий...\n"
        "Фаза 4/5: генерация алертов...\n"
        "Traceback (most recent call last):\n"
    )
    assert [p["state"] for p in _pipeline_phases(out, "failed")] == [
        "skipped", "skipped", "done", "error", "pending",
    ]
    assert _pipeline_failed_phase(out) == "4/5: генерация алертов"

    run_out = (
        "Фаза 1/5: сбор и анализ новостей...\n"
        "Фаза 2/5: синхронизация свечей MOEX (500 бумаг)...\n"
    )
    assert [p["state"] for p in _pipeline_phases(run_out, "running")] == [
        "done", "running", "pending", "pending", "pending",
    ]
    assert _pipeline_phases("обычный скрипт без фаз", "failed") is None
    assert _pipeline_failed_phase(None) is None


def test_pipeline_phase2_task_states_and_progress():
    """Фаза 2: статусы подзадач акций/фьючерсов и прогресс от маркеров в логе."""
    from app.web.router import _pipeline_phases

    # Фаза 2 в процессе: акции ещё не завершены -> первая running, фьючерсы ждут
    out_start = (
        "Фаза 1/5: сбор и анализ новостей...\n"
        "Новости: 3 сохранено\n"
        "Фаза 2/5: синхронизация свечей MOEX (500 бумаг)...\n"
        "  [акции 1/50] AFLT: свечи синхронизированы\n"
    )
    ph = _pipeline_phases(out_start, "running")
    assert ph[1]["state"] == "running"
    assert [t["state"] for t in ph[1]["tasks"]] == ["running", "waiting"]
    assert ph[1]["pct"] == 0

    # Акции завершены, фьючерсы начались -> первая done, вторая running
    out_mid = out_start + "Синхронизация акций: 50 свечей обновлено\n  [фьючерсы 1/10] W4V6: свечи синхронизированы\n"
    ph = _pipeline_phases(out_mid, "running")
    assert [t["state"] for t in ph[1]["tasks"]] == ["done", "running"]
    assert ph[1]["pct"] == 50

    # Обе подзадачи завершены -> 100%
    out_done = out_mid + "Синхронизация фьючерсов: 10 свечей обновлено\n"
    ph = _pipeline_phases(out_done, "running")
    assert [t["state"] for t in ph[1]["tasks"]] == ["done", "done"]
    assert ph[1]["pct"] == 100
    assert [t["count"] for t in ph[1]["tasks"]] == ["50", "10"]


def test_pipeline_phase1_aux_tasks_filtered():
    """Фаза 1: без флагов Telegram/сайты скрыты, прогресс достигает 100%."""
    from app.web.router import _pipeline_phases

    out = (
        "Фаза 1/5: сбор и анализ новостей...\n"
        "Новости: 5 сохранено\n"
        "Фаза 2/5: синхронизация свечей MOEX (1 бумаг)...\n"
    )
    ph = _pipeline_phases(out, "running")
    # Только «Сбор RSS новостей»; Telegram/сайты не показаны
    titles = [t["title"] for t in ph[0]["tasks"]]
    assert titles == ["Сбор RSS новостей"]

    out_tg = (
        "Фаза 1/5: сбор и анализ новостей...\n"
        "Новости: 5 сохранено\n"
        "Фаза 1b/5: Telegram-каналы...\n"
        "Telegram-новости: 2 сохранено\n"
    )
    ph = _pipeline_phases(out_tg, "running")
    assert [t["state"] for t in ph[0]["tasks"]] == ["done", "done"]
