"""Сидинг тестовой БД для e2e-тестов веб-интерфейса (Playwright Test Runner).

Создаёт схему и наполняет БД данными, на которых работают e2e-сценарии:
граф и справочник бумаг, свечи (SBER, AFLT), макро-события, источник и
статья о Сбербанке, стратегия SBER, пользователи admin/user.
Идемпотентен (повторный запуск не дублирует данные).

Использование: `DATABASE_URL=sqlite+aiosqlite:///... python -m scripts.e2e_seed`
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import hash_password
from app.db.connection import Base
from app.db.models import (
    Article,
    ArticleEntity,
    BrokerConnection,
    BrokerAccount,
    BrokerPortfolioSnapshot,
    Entity,
    FuturesTemplate,
    GraphCandidate,
    GraphCandidateEvidence,
    MacroEvent,
    MarketCandle,
    MarketOpenPosition,
    MarketOpenPositionClientGroup,
    RealtimeConfig,
    ScenarioEvaluation,
    ScenarioSnapshot,
    Security,
    Source,
    Strategy,
    TechAnalysis,
    TechAnalysisBatch,
    User,
    UserSource,
)
from app.graph.service import seed_graph
from app.config import get_settings

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"  # pragma: allowlist secret
USER_USERNAME = "user"
USER_PASSWORD = "user123"  # pragma: allowlist secret


async def seed(db_url: str) -> None:
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await seed_graph(session)
        if (
            await session.scalar(select(User).where(User.username == ADMIN_USERNAME))
            is None
        ):
            session.add(
                User(
                    username=ADMIN_USERNAME,
                    password_hash=hash_password(ADMIN_PASSWORD),
                    role="admin",
                )
            )
        if (
            await session.scalar(select(User).where(User.username == USER_USERNAME))
            is None
        ):
            session.add(
                User(
                    username=USER_USERNAME,
                    password_hash=hash_password(USER_PASSWORD),
                    role="user",
                )
            )
        sber = await session.scalar(select(Security).where(Security.ticker == "SBER"))
        if (
            await session.get(RealtimeConfig, 1) is None
        ):
            session.add(RealtimeConfig(id=1))
        if sber is not None:
            existing = await session.scalar(
                select(Strategy).where(Strategy.security_id == sber.id)
            )
            if existing is None:
                session.add(
                    Strategy(
                        security_id=sber.id,
                        verdict="BUY",
                        horizon="medium",
                        confidence="medium",
                        model_version="mvp-0.1",
                        rationale_summary="e2e seed",
                    )
                )
        # Top-5: шаблон акций (kind=stock) + успешный Теханализ по SBER + батч
        tpl = await session.scalar(select(FuturesTemplate).where(FuturesTemplate.name == "e2e_top5"))
        if tpl is None:
            session.add(FuturesTemplate(name="e2e_top5", tickers="SBER", kind="stock"))
            await session.flush()
            tpl = await session.scalar(select(FuturesTemplate).where(FuturesTemplate.name == "e2e_top5"))
        ta = await session.scalar(
            select(TechAnalysis).where(TechAnalysis.ticker == "SBER", TechAnalysis.batch_id.isnot(None))
        )
        confirmation_data = {
            "scenario_a": {"title": "A", "entry": "100-105", "stop": "98", "targets": "115", "why": "ok", "probability": 0.65, "rr": 2.5},
            "scenario_b": {"title": "B", "entry": "98", "stop": "105", "targets": "90", "why": "альтернатива", "probability": 0.25, "rr": 1.5},
            "scenario_c": {},
            "confirmation_plans": {
                "a": {"direction": "buy", "timeframe": "1ч", "level_label": "зона 100–105", "steps": [
                    {"kind": "approach", "label": "Подход", "detail": "Цена подходит к зоне"},
                    {"kind": "close", "label": "Закрытие", "detail": "Свеча закрывается выше зоны"},
                    {"kind": "entry", "label": "Вход", "detail": "После подтверждения"},
                ], "closed_candles": 1, "wait_minutes": None, "confirmations": ["объём растёт"], "cancellations": ["закрытие ниже зоны"]},
                "b": {"direction": "sell", "timeframe": None, "level_label": "зона 98", "steps": [
                    {"kind": "approach", "label": "Подход", "detail": "Цена подходит к зоне"},
                    {"kind": "breakout", "label": "Пробой", "detail": "Цена уходит ниже зоны"},
                    {"kind": "entry", "label": "Вход", "detail": "После пробоя"},
                ], "closed_candles": None, "wait_minutes": None, "confirmations": [], "cancellations": ["возврат выше зоны"]},
            },
        }
        confirmation_response = "## 1. Рынок\nКонтекст.\n\n## 10. Что должно подтвердить сделку\nЖдать закрытия свечи выше зоны.\n\n## 10. Риски\nУчитывать риск."
        if ta is None:
            batch = await session.scalar(
                select(TechAnalysisBatch).where(TechAnalysisBatch.template_id == tpl.id)
            )
            if batch is None:
                batch = TechAnalysisBatch(user_id=None, template_id=tpl.id, status="success")
                session.add(batch)
                await session.flush()
            session.add(
                TechAnalysis(
                    ticker="SBER",
                    status="success",
                    stage="done",
                    verdict="BUY",
                    entry="100-105",
                    tp="115",
                    sl="98",
                    scenario_json=json.dumps(confirmation_data, ensure_ascii=False),
                    response_md=confirmation_response,
                    await_confirmation=True,
                    model="e2e",
                    finished_at=datetime.now(timezone.utc),
                    batch_id=batch.id,
                )
            )
        else:
            ta.scenario_json = json.dumps(confirmation_data, ensure_ascii=False)
            ta.response_md = confirmation_response
            ta.await_confirmation = True
            ta.finished_at = datetime.now(timezone.utc)
        retry_template = await session.scalar(select(FuturesTemplate).where(FuturesTemplate.name == "e2e_top5_retry_future"))
        if retry_template is None:
            retry_template = FuturesTemplate(name="e2e_top5_retry_future", tickers="FUTRETRY", kind="futures")
            session.add(retry_template)
            await session.flush()
        retry_batch = await session.scalar(select(TechAnalysisBatch).where(TechAnalysisBatch.template_id == retry_template.id))
        if retry_batch is None:
            retry_batch = TechAnalysisBatch(template_id=retry_template.id, status="failed")
            session.add(retry_batch)
            await session.flush()
        retry_analysis = await session.scalar(select(TechAnalysis).where(TechAnalysis.batch_id == retry_batch.id))
        if retry_analysis is None:
            session.add(TechAnalysis(
                ticker="FUTRETRY", status="failed", stage="done", error="тестовая ошибка",
                batch_id=retry_batch.id,
            ))
        filter_template = await session.scalar(select(FuturesTemplate).where(FuturesTemplate.name == "e2e_top5_filter"))
        if filter_template is None:
            filter_template = FuturesTemplate(name="e2e_top5_filter", tickers="FILT1,FILT2,FILT3,FILT4,SBER,FILT5", kind="stock")
            session.add(filter_template)
        thirty_tickers = [f"THIRTY{i:02d}" for i in range(30)]
        thirty_template = await session.scalar(select(FuturesTemplate).where(FuturesTemplate.name == "e2e_top5_thirty"))
        if thirty_template is None:
            session.add(FuturesTemplate(name="e2e_top5_thirty", tickers=",".join(thirty_tickers), kind="stock"))
        for ticker in thirty_tickers:
            analysis = await session.scalar(
                select(TechAnalysis).where(TechAnalysis.ticker == ticker, TechAnalysis.model == "e2e-thirty")
            )
            if analysis is None:
                analysis = TechAnalysis(ticker=ticker, status="success", stage="done", verdict="BUY", model="e2e-thirty")
                session.add(analysis)
            analysis.scenario_json = json.dumps({
                "scenario_a": {"entry": "100", "stop": "98", "targets": "108", "why": "тест", "probability": 0.6, "rr": 2.0},
                "scenario_b": {}, "scenario_c": {},
            }, ensure_ascii=False)
            analysis.finished_at = datetime.now(timezone.utc)
        filter_cases = [
            ("FILT1", "BUY", 0.8, 4.0),
            ("FILT2", "BUY", 0.75, 3.5),
            ("FILT3", "SELL", 0.7, 3.0),
            ("FILT4", "WAIT", 0.6, 3.0),
            ("FILT5", "BUY", 0.5, 2.0),
        ]
        for ticker, verdict, probability, rr in filter_cases:
            analysis = await session.scalar(
                select(TechAnalysis).where(TechAnalysis.ticker == ticker, TechAnalysis.model == "e2e-filter")
            )
            scenario_json = json.dumps({
                "scenario_a": {"entry": "100", "stop": "98", "targets": "108", "why": "тестовый сценарий", "probability": probability, "rr": rr},
                "scenario_b": {}, "scenario_c": {},
            }, ensure_ascii=False)
            if analysis is None:
                session.add(TechAnalysis(
                    ticker=ticker, status="success", stage="done", verdict=verdict,
                    scenario_json=scenario_json, model="e2e-filter", finished_at=datetime.now(timezone.utc),
                ))
            else:
                analysis.scenario_json = scenario_json
                analysis.finished_at = datetime.now(timezone.utc)
        legacy = await session.scalar(
            select(TechAnalysis).where(TechAnalysis.ticker == "AFLT", TechAnalysis.model == "e2e-legacy")
        )
        if legacy is None:
            session.add(
                TechAnalysis(
                    ticker="AFLT",
                    status="success",
                    stage="done",
                    verdict="WAIT",
                    scenario_json="[]",
                    response_md="## 10. Что должно подтвердить сделку\nЖдать подтверждения без заданного срока.",
                    model="e2e-legacy",
                    finished_at=datetime.now(timezone.utc),
                )
            )
        macro_seed = [
            {
                "event_type": "central_bank_meeting",
                "title": "Заседание Банка России по ключевой ставке",
                "region": "RU",
                "expected_impact": "high",
                "market_wide": True,
            },
            {
                "event_type": "cpi",
                "title": "Публикация индекса потребительских цен РФ",
                "region": "RU",
                "expected_impact": "medium",
                "market_wide": True,
            },
            {
                "event_type": "cpi",
                "title": "Публикация CPI США",
                "region": "US",
                "expected_impact": "low",
                "market_wide": True,
            },
        ]
        for item in macro_seed:
            if (
                await session.scalar(
                    select(MacroEvent).where(MacroEvent.title == item["title"])
                )
                is None
            ):
                session.add(
                    MacroEvent(
                        event_type=item["event_type"],
                        title=item["title"],
                        event_time=datetime.now(timezone.utc) + timedelta(days=3),
                        region=item["region"],
                        expected_impact=item["expected_impact"],
                        market_wide=item["market_wide"],
                    )
                )
        candle_seed = {
            "SBER": {"start": 280.0, "step": 0.5},
            "AFLT": {"start": 45.0, "step": -0.15},
        }
        today = datetime.now(timezone.utc).date()
        for ticker, cfg in candle_seed.items():
            sec = await session.scalar(select(Security).where(Security.ticker == ticker))
            if sec is None:
                continue
            if (
                await session.scalar(
                    select(MarketCandle).where(MarketCandle.security_id == sec.id)
                )
                is not None
            ):
                continue
            price = cfg["start"]
            for i in range(60):
                day = today - timedelta(days=59 - i)
                price = price + cfg["step"] + (i % 5) * 0.12
                session.add(
                    MarketCandle(
                        security_id=sec.id,
                        trading_date=day,
                        open=round(price - 0.4, 2),
                        high=round(price + 0.6, 2),
                        low=round(price - 0.6, 2),
                        close=round(price, 2),
                        volume=100_000 + i * 1_000,
                    )
                )
        if sber is not None and (
            await session.scalar(
                select(MarketOpenPosition).where(
                    MarketOpenPosition.security_id == sber.id
                )
            )
            is None
        ):
            for i in range(6):
                day = today - timedelta(days=5 - i)
                physical_long = 25_000 + i * 350
                physical_short = 16_500 + i * 120
                juridical_long = 7_500 + i * 100
                juridical_short = 16_000 + i * 330
                summary = (
                    physical_long
                    + physical_short
                    + juridical_long
                    + juridical_short
                )
                session.add(
                    MarketOpenPosition(
                        security_id=sber.id,
                        trading_date=day,
                        open_position=summary // 2,
                    )
                )
                for group, long_pos, short_pos in (
                    ("physical", physical_long, physical_short),
                    ("juridical", juridical_long, juridical_short),
                ):
                    session.add(
                        MarketOpenPositionClientGroup(
                            security_id=sber.id,
                            trading_date=day,
                            client_group=group,
                            long_pos=long_pos,
                            short_pos=short_pos,
                            net_pos=long_pos - short_pos,
                            participants=100 if group == "physical" else 10,
                            participants_long=100 + i if group == "physical" else 10 + i,
                            participants_short=80 + i if group == "physical" else 8 + i,
                            summary=summary,
                        )
                    )
        source = await session.scalar(select(Source).where(Source.name == "e2e_feed"))
        if source is None:
            source = Source(
                name="e2e_feed",
                kind="rss",
                reputation_score=0.7,
                is_active=True,
                config={},
            )
            session.add(source)
            await session.flush()
        user_row = await session.scalar(
            select(User).where(User.username == USER_USERNAME)
        )
        site = await session.scalar(select(Source).where(Source.name == "e2e_site"))
        if site is None:
            site = Source(
                name="e2e_site",
                kind="website",
                reputation_score=0.5,
                is_active=True,
                last_status="ok",
                config={"url": "https://e2e.example/press"},
            )
            session.add(site)
            await session.flush()
        if user_row is not None and site is not None:
            link = await session.scalar(
                select(UserSource).where(
                    UserSource.user_id == user_row.id,
                    UserSource.source_id == site.id,
                )
            )
            if link is None:
                session.add(UserSource(user_id=user_row.id, source_id=site.id))
        article = await session.scalar(
            select(Article).where(Article.url == "https://e2e.example/sber-news")
        )
        if article is None:
            article = Article(
                title="Сбербанк отчитался о росте прибыли",
                text="E2E-статья: Сбербанк показал рост чистой прибыли.",
                url="https://e2e.example/sber-news",
                source_id=source.id,
                source_reputation=0.7,
                published_at=datetime.now(timezone.utc) - timedelta(hours=5),
                language="ru",
            )
            session.add(article)
            await session.flush()
            sber_entity = await session.scalar(
                select(Entity).where(Entity.name == "Сбербанк")
            )
            if sber_entity is not None:
                session.add(
                    ArticleEntity(
                        article_id=article.id,
                        entity_id=sber_entity.id,
                        sentiment="positive",
                        topic="результаты",
                        impact=0.6,
                        snippet="Сбербанк отчитался",
                        entity_role="primary",
                    )
                )
        e2e_entities: dict[str, Entity] = {}
        for name in ("E2E-фактор", "E2E-сектор", "E2E-компания", "E2E-риск"):
            entity = await session.scalar(select(Entity).where(Entity.name == name))
            if entity is None:
                entity = Entity(name=name, type="event")
                session.add(entity)
                await session.flush()
            e2e_entities[name] = entity
        candidate_specs = (
            ("E2E-фактор", "E2E-сектор", "pending", "Кандидат для принятия"),
            ("E2E-сектор", "E2E-компания", "pending", "Кандидат для отклонения"),
            ("E2E-риск", "E2E-компания", "pending", "Кандидат для дополнительной проверки"),
        )
        for from_name, to_name, status, rationale in candidate_specs:
            candidate = await session.scalar(
                select(GraphCandidate).where(GraphCandidate.rationale == rationale)
            )
            if candidate is None:
                candidate = GraphCandidate(
                    from_entity_id=e2e_entities[from_name].id,
                    to_entity_id=e2e_entities[to_name].id,
                    direction="positive",
                    strength="medium",
                    kind="direct",
                    confidence=0.8,
                    rationale=rationale,
                    status=status,
                    evidence_count=1,
                )
                session.add(candidate)
                await session.flush()
                session.add(
                    GraphCandidateEvidence(
                        candidate_id=candidate.id,
                        article_id=article.id,
                        quote="E2E-доказательство связи",
                        rationale=rationale,
                        confidence=0.8,
                        strength="medium",
                    )
                )
        from app.tech_analysis.outcomes import capture
        owner = await session.scalar(select(User).where(User.username == USER_USERNAME))
        if await session.scalar(select(BrokerConnection.id).where(BrokerConnection.user_id == owner.id)) is None:
            session.add(BrokerConnection(user_id=owner.id, provider="t_invest", environment="sandbox", token_ciphertext="e2e-unused", token_nonce="e2e-unused", token_key_id="e2e-unused"))
        production_user = await session.scalar(select(User).where(User.username == "broker-production"))
        if production_user is None:
            production_user = User(username="broker-production", password_hash=hash_password(USER_PASSWORD), role="user")
            session.add(production_user)
            await session.flush()
        production = await session.scalar(select(BrokerConnection).where(BrokerConnection.user_id == production_user.id, BrokerConnection.environment == "production"))
        if production is None:
            production = BrokerConnection(user_id=production_user.id, provider="t_invest", environment="production", token_ciphertext="e2e-unused", token_nonce="e2e-unused", token_key_id="e2e-unused", selected_account_id="production-1234")
            session.add(production)
            await session.flush()
            for external_id, account_name in [("production-1234", "Основной"), ("production-5678", "Второй")]:
                account = BrokerAccount(connection_id=production.id, external_id=external_id, name=account_name, status="ACCOUNT_STATUS_OPEN", access_level="ACCOUNT_ACCESS_LEVEL_READ_ONLY")
                session.add(account)
                await session.flush()
                if external_id == "production-1234":
                    session.add(BrokerPortfolioSnapshot(account_id=account.id, fetched_at=datetime.now(timezone.utc), data={"cash": [{"amount": "1500.50", "currency": "rub"}], "positions": [], "total": {"amount": "2500.50", "currency": "rub"}}))
        if await session.scalar(select(BrokerConnection.id).where(BrokerConnection.user_id == production_user.id, BrokerConnection.environment == "sandbox")) is None:
            session.add(BrokerConnection(user_id=production_user.id, provider="t_invest", environment="sandbox", token_ciphertext="e2e-unused", token_nonce="e2e-unused", token_key_id="e2e-unused"))
        if await session.scalar(select(TechAnalysis.id).where(TechAnalysis.ticker == "OUTCOMEE2E")) is None:
            batch = TechAnalysisBatch(user_id=owner.id, status="success")
            session.add(batch)
            await session.flush()
            analysis = TechAnalysis(user_id=owner.id, batch_id=batch.id, ticker="OUTCOMEE2E", status="success", is_future=False, finished_at=datetime.now(timezone.utc), model="e2e-outcomes", scenario_json=json.dumps({"scenario_a": {"entry": "100", "stop": "95", "targets": "110"}, "execution_plans": {"a": {"direction": "buy", "kind": "touch", "entry": 100, "stop": 95, "target": 110}}}))
            session.add(analysis)
            await session.flush()
            await capture(session, analysis)
        outcome = await session.scalar(select(ScenarioEvaluation).join(ScenarioSnapshot).where(ScenarioSnapshot.ticker == "OUTCOMEE2E", ScenarioSnapshot.scenario_key == "a"))
        outcome.detail = "Календарь MOEX требует авторизации"
        outcome.market_context = {"calendar": {"provider": "t_invest", "environment": "sandbox"}}
        await session.commit()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed(get_settings().database_url))
    print("E2E-БД засижена", flush=True)
