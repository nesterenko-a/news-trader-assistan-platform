import argparse
import asyncio

from sqlalchemy import select

from app.db.connection import SessionLocal, init_db
from app.db.models import Security
from app.market.prices import sync_security_prices


async def main() -> None:
    parser = argparse.ArgumentParser(description="Sync daily candles from MOEX ISS")
    parser.add_argument("--days", type=int, default=5, help="lookback days")
    args = parser.parse_args()

    await init_db()
    async with SessionLocal() as session:
        tickers = [
            s.ticker
            for s in (
                await session.scalars(select(Security).order_by(Security.ticker))
            ).all()
        ]
        total = 0
        for ticker in tickers:
            inserted = await sync_security_prices(session, ticker, args.days)
            total += inserted
            print(f"{ticker}: +{inserted}")
        print(f"total synced: {total} candles")


if __name__ == "__main__":
    asyncio.run(main())
