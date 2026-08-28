"""Сервис реалтайм-актуализации рыночных данных (docs/24).

Переиспользует существующие примитивы:
  - MOEXClient().fetch_quote  (LAST/OHLC/объём дня) -> RealTimeQuote (upsert);
  - sync_security_prices       (свечи) и sync_security_oi (OI фьючерсов);
  - состав инструментов = watchlist всех пользователей + mvp-тикеры (акции)
    и фьючерсы выбранного шаблона (FuturesTemplate, kind=futures).
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import FuturesTemplate, RealTimeQuote, RealtimeConfig, Security, WatchlistItem
from app.market.moex import MOEXClient

settings = get_settings()


async def ensure_config(session: AsyncSession) -> RealtimeConfig:
    """Возвращает singleton-настройку realtime (docs/24 §6.1), создавая при отсутствии."""
    config = await session.get(RealtimeConfig, 1)
    if config is None:
        config = RealtimeConfig(id=1)
        session.add(config)
        await session.commit()
    return config


def _futures_tickers(template: FuturesTemplate | None) -> list[str]:
    if template is None:
        return []
    return [
        t.strip().upper()
        for t in (template.tickers or "").replace(";", ",").split(",")
        if t.strip()
    ]


async def compute_scope(
    session: AsyncSession, config: RealtimeConfig | None = None
) -> tuple[list[Security], list[Security]]:
    """Состав инструментов для реалтайма.

    Акции: объединение watchlist всех пользователей и mvp-тикеров (дедуп
    по security_id). Фьючерсы: тикеры выбранного шаблона (kind=futures).
    """
    if config is None:
        config = await ensure_config(session)

    # Акции из watchlist всех пользователей
    watchlist_ids = (
        await session.scalars(
            select(WatchlistItem.security_id).distinct()
        )
    ).all()
    stock_ids = list(watchlist_ids)

    # Акции из mvp_tickers
    mvp = await session.scalars(
        select(Security.id).where(
            Security.security_type == "stock", Security.ticker.in_(settings.ticker_list)
        )
    )
    for sid in mvp.all():
        if sid not in stock_ids:
            stock_ids.append(sid)

    stocks: list[Security] = []
    if stock_ids:
        securities = (
            await session.scalars(
                select(Security).where(Security.security_type == "stock", Security.id.in_(stock_ids))
            )
        ).all()
        stocks = list(securities)

    # Фьючерсы из выбранного шаблона
    futures: list[Security] = []
    if config.futures_template_id is not None:
        template = await session.get(FuturesTemplate, config.futures_template_id)
        tickers = _futures_tickers(template)
        if tickers:
            securities = (
                await session.scalars(
                    select(Security).where(
                        Security.security_type == "futures", Security.ticker.in_(tickers)
                    )
                )
            ).all()
            futures = list(securities)

    return stocks, futures


async def upsert_quote(session: AsyncSession, security_id: int, quote: dict) -> bool:
    """Записывает/обновляет live-котировку по security_id (upsert, одна запись на бумагу).

    Если MOEX не вернул LAST (биржа закрыта/нет данных) — предыдущее значение
    last не перезаписывается, остальные поля и метка обновляются.
    """
    quote_row = await session.get(RealTimeQuote, security_id)
    if quote_row is None:
        quote_row = RealTimeQuote(security_id=security_id)
        session.add(quote_row)
    quote_row.open = quote.get("open")
    quote_row.high = quote.get("high")
    quote_row.low = quote.get("low")
    quote_row.volume = quote.get("volume") or 0
    if quote.get("price") is not None:
        quote_row.last = quote["price"]
    quote_row.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return True


async def fetch_scope_quotes(
    session: AsyncSession, stocks: list[Security], futures: list[Security]
) -> int:
    """Обновляет live-котировки по составу (акции + фьючерсы). Возвращает число обновлённых."""
    scope = stocks + futures
    if not scope:
        return 0
    client = MOEXClient()
    updated = 0
    for security in scope:
        try:
            quote = await client.fetch_quote(security.ticker)
        except Exception:
            quote = None
        if quote is None:
            continue
        await upsert_quote(session, security.id, quote)
        updated += 1
    return updated
