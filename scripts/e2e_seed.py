"""Сидинг тестовой БД для e2e-тестов веб-интерфейса (Playwright Test Runner).

Создаёт схему и наполняет БД данными, на которых работают e2e-сценарии:
граф и справочник бумаг, свечи (SBER, AFLT), макро-события, источник и
статья о Сбербанке, стратегия SBER, пользователи admin/user.
Идемпотентен (повторный запуск не дублирует данные).

Использование: `DATABASE_URL=sqlite+aiosqlite:///... python -m scripts.e2e_seed`
"""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import hash_password
from app.db.connection import Base
from app.db.models import (
    Article,
    ArticleEntity,
    Entity,
    FuturesTemplate,
    MacroEvent,
    MarketCandle,
    MarketOpenPosition,
    MarketOpenPositionClientGroup,
    RealtimeConfig,
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
ADMIN_PASSWORD = "admin123"
USER_USERNAME = "user"
USER_PASSWORD = "user123"


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
                    scenario_json=(
                        '{"scenario_a":{"title":"A","entry":"100-105","stop":"98",'
                        '"targets":"115","why":"ok","probability":0.65,"rr":2.5},'
                        '"scenario_b":{},"scenario_c":{}}'
                    ),
                    model="e2e",
                    finished_at=datetime.now(timezone.utc),
                    batch_id=batch.id,
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
        await session.commit()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed(get_settings().database_url))
    print("E2E-БД засижена", flush=True)
