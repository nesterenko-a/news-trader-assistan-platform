from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MarketCandle, MarketOpenPosition, Security
from app.market.moex import MOEXClient


async def ensure_futures_security(
    session: AsyncSession, ticker: str, shortname: str = ""
) -> Security:
    """Возвращает Security по тикеру; если фьючерса нет в справочнике — создаёт."""
    security = await session.scalar(select(Security).where(Security.ticker == ticker))
    if security is None:
        security = Security(
            ticker=ticker,
            name=shortname or ticker,
            market="MOEX",
            security_type="futures",
            sector="",
            currency="RUB",
            aliases=[],
        )
        session.add(security)
        await session.flush()
    return security


async def sync_security_oi(
    session: AsyncSession,
    ticker: str,
    days: int | None = None,
    since: date | None = None,
) -> int:
    """Скачивает историю открытых позиций фьючерса с ISS и сохраняет в market_open_positions."""
    ticker = ticker.upper()

    if since is not None:
        from_date = since
    else:
        from_date = date.today() - timedelta(days=days or 30)

    rows = await MOEXClient().fetch_open_positions(
        ticker,
        from_date=from_date,
        till_date=date.today(),
    )
    if not rows:
        return 0

    security = await ensure_futures_security(session, ticker, rows[-1]["shortname"])

    inserted = 0
    for row in rows:
        if row["open_position"] > 0 or row["open_position_value"] is not None:
            existing = await session.scalar(
                select(MarketOpenPosition).where(
                    MarketOpenPosition.security_id == security.id,
                    MarketOpenPosition.trading_date == row["date"],
                )
            )
            if existing is None:
                session.add(
                    MarketOpenPosition(
                        security_id=security.id,
                        trading_date=row["date"],
                        open_position=row["open_position"],
                        open_position_value=row["open_position_value"],
                        source="iss",
                    )
                )
                inserted += 1

        if row["close"] is not None:
            candle = await session.scalar(
                select(MarketCandle).where(
                    MarketCandle.security_id == security.id,
                    MarketCandle.trading_date == row["date"],
                )
            )
            if candle is None:
                session.add(
                    MarketCandle(
                        security_id=security.id,
                        trading_date=row["date"],
                        open=row["open"],
                        high=row["high"],
                        low=row["low"],
                        close=row["close"],
                        volume=row["volume"],
                    )
                )

    await session.commit()
    return inserted
