"""Сервис источников новостей: каталог sources, персональные списки user_sources."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.rss import DEFAULT_FEEDS
from app.db.models import Source, UserSource

SOURCE_CATEGORIES = ["биржа", "геополитика", "погода"]

DEFAULT_SITES: list[dict] = []


def default_site_dicts() -> list[dict]:
    return [
        {
            "name": site["name"],
            "kind": "website",
            "reputation": site["reputation"],
            "config": {"url": site["url"]},
            "category": site.get("category", ""),
        }
        for site in DEFAULT_SITES
    ]


def default_feed_dicts() -> list[dict]:
    return [
        {
            "name": feed["name"],
            "kind": "rss",
            "reputation": feed["reputation"],
            "config": {"url": feed["url"]},
            "category": feed.get("category", ""),
        }
        for feed in DEFAULT_FEEDS
    ]


async def ensure_sources(session: AsyncSession, items: list[dict]) -> dict[str, Source]:
    """Гарантирует наличие записей каталога; возвращает {имя: Source}."""
    by_name: dict[str, Source] = {}
    for item in items:
        source = await session.scalar(select(Source).where(Source.name == item["name"]))
        if source is None:
            source = Source(
                name=item["name"],
                kind=item["kind"],
                reputation_score=item.get("reputation", 0.5),
                config=item.get("config", {}),
                category=item.get("category", ""),
            )
            session.add(source)
            await session.flush()
        else:
            config = dict(source.config or {})
            url = item.get("config", {}).get("url")
            if url and config.get("url") != url:
                config["url"] = url
                source.config = config
        by_name[item["name"]] = source
    return by_name


async def add_sources_to_user(
    session: AsyncSession, user_id: int, items: list[dict]
) -> int:
    """Добавляет источники в персональный список пользователя (без дублей)."""
    by_name = await ensure_sources(session, items)
    added = 0
    for source in by_name.values():
        exists = await session.scalar(
            select(UserSource).where(
                UserSource.user_id == user_id, UserSource.source_id == source.id
            )
        )
        if exists is None:
            session.add(UserSource(user_id=user_id, source_id=source.id))
            added += 1
    if added:
        await session.commit()
    return added


async def add_default_sources_for_user(session: AsyncSession, user_id: int) -> int:
    """Стандартные ленты (DEFAULT_FEEDS) в список нового пользователя."""
    return await add_sources_to_user(session, user_id, default_feed_dicts())


async def restore_default_sources(session: AsyncSession, user_id: int) -> int:
    """Кнопка «Вернуть стандартные ленты»: добавляет недостающие DEFAULT_FEEDS."""
    return await add_default_sources_for_user(session, user_id)


async def restore_default_sites(session: AsyncSession, user_id: int) -> int:
    """Кнопка «Вернуть стандартные сайты»: добавляет недостающие DEFAULT_SITES."""
    return await add_sources_to_user(session, user_id, default_site_dicts())


async def get_rss_feeds(session: AsyncSession) -> list[dict]:
    """Активные RSS-ленты из каталога (для коллектора); [] если пусто.

    Каждая лента: {name, url, reputation, category, use_llm} — use_llm включает
    LLM-разбор при неудаче штатного парсинга.
    """
    return await _active_sources(session, "rss")


async def get_website_sources(session: AsyncSession) -> list[dict]:
    """Активные сайты компаний из каталога (для коллектора); [] если пусто.

    Каждый сайт: {name, url, reputation, category, use_llm, use_browser} —
    извлечение записей со страницы-списка выполняется через LLM.
    """
    return await _active_sources(session, "website")


async def _active_sources(session: AsyncSession, kind: str) -> list[dict]:
    rows = await session.scalars(
        select(Source).where(Source.kind == kind, Source.is_active.is_(True))
    )
    feeds = []
    for source in rows:
        url = (source.config or {}).get("url")
        if not url:
            continue
        feeds.append(
            {
                "name": source.name,
                "url": url,
                "reputation": source.reputation_score,
                "category": source.category or "",
                "use_llm": bool(source.use_llm),
                "use_browser": bool(source.use_browser),
            }
        )
    return feeds


async def user_sources(
    session: AsyncSession,
    user_id: int,
    kind: str | None = None,
    category: str | None = None,
) -> list[Source]:
    """Источники в персональном списке пользователя (с фильтрами)."""
    query = (
        select(Source)
        .join(UserSource, UserSource.source_id == Source.id)
        .where(UserSource.user_id == user_id)
        .order_by(Source.name)
    )
    if kind:
        query = query.where(Source.kind == kind)
    if category is not None:
        query = query.where(Source.category == category)
    return list((await session.scalars(query)).all())
