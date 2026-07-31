from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MarketCandle, Security
from app.market.moex import MOEXClient


async def sync_security_prices(
    session: AsyncSession,
    ticker: str,
    days: int | None = None,
    since: date | None = None,
) -> int:
    security = await session.scalar(select(Security).where(Security.ticker == ticker))
    if security is None:
        return 0

    if since is not None:
        from_date = since
    else:
        from_date = date.today() - timedelta(days=days or 5)

    candles = await MOEXClient().fetch_candles(
        ticker,
        from_date=from_date,
        till_date=date.today(),
    )

    inserted = 0
    for candle in candles:
        existing = await session.scalar(
            select(MarketCandle).where(
                MarketCandle.security_id == security.id,
                MarketCandle.trading_date == candle["date"],
            )
        )
        if existing is None:
            session.add(
                MarketCandle(
                    security_id=security.id,
                    trading_date=candle["date"],
                    open=candle["open"],
                    high=candle["high"],
                    low=candle["low"],
                    close=candle["close"],
                    volume=candle["volume"],
                )
            )
            inserted += 1

    await session.commit()
    return inserted
