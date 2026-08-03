import argparse
import asyncio
from datetime import date

from sqlalchemy import select

from app.db.connection import SessionLocal, init_db
from app.db.models import Security
from app.market.prices import sync_security_prices


async def main() -> None:
    parser = argparse.ArgumentParser(description="Sync daily candles from MOEX ISS")
    parser.add_argument("--days", type=int, default=5, help="lookback days")
    parser.add_argument(
        "--from",
        dest="from_date",
        default="",
        help="start date YYYY-MM-DD (full history), overrides --days",
    )
    args = parser.parse_args()

    since = date.fromisoformat(args.from_date) if args.from_date else None

    await init_db()
    async with SessionLocal() as session:
        tickers = [
            s.ticker
            for s in (
                await session.scalars(select(Security).order_by(Security.ticker))
            ).all()
        ]
        print(f"Синхронизация свечей MOEX ({len(tickers)} бумаг)...", flush=True)
        total = 0
        for i, ticker in enumerate(tickers, 1):
            if since is not None:
                inserted = await sync_security_prices(session, ticker, since=since)
            else:
                inserted = await sync_security_prices(session, ticker, days=args.days)
            total += inserted
            print(f"  [{i}/{len(tickers)}] {ticker}: +{inserted} свечей", flush=True)
        print(f"Итого обновлено: {total} свечей")


if __name__ == "__main__":
    asyncio.run(main())
