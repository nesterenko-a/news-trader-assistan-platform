import argparse
import asyncio
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select

from app.db.connection import SessionLocal, init_db
from app.db.models import Security
from app.market.prices import sync_security_prices
from app.strategy.engine import generate_strategy
from scripts.collect_news import _parse_since, collect_news

PRICE_LOOKBACK_DAYS = 5


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Daily pipeline: news, prices, strategies"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=0,
        help="collect news published in the last N days (0 = no limit)",
    )
    parser.add_argument(
        "--from",
        dest="from_date",
        default="",
        help="collect news published since YYYY-MM-DD (overrides --days)",
    )
    args = parser.parse_args()

    since = _parse_since(args)
    if since is not None:
        print(f"Collecting news published since {since.isoformat()}")

    await init_db()
    async with SessionLocal() as session:
        stored = await collect_news(session, since=since)
        print(f"news: {stored} stored")

        tickers = [
            s.ticker
            for s in (
                await session.scalars(select(Security).order_by(Security.ticker))
            ).all()
        ]

        synced = 0
        for ticker in tickers:
            synced += await sync_security_prices(session, ticker, PRICE_LOOKBACK_DAYS)
        print(f"prices: {synced} candles synced")

        stored_strategies = []
        rejected_strategies = []
        for ticker in tickers:
            result = await generate_strategy(session, ticker)
            verdict = result["strategy"]["verdict"]
            if verdict == "INSUFFICIENT_DATA":
                rejected_strategies.append(ticker)
                print(
                    f"strategy {ticker}: REJECTED (insufficient data)",
                    flush=True,
                )
            else:
                stored_strategies.append(ticker)
                print(
                    f"strategy {ticker}: STORED "
                    f"verdict={verdict} "
                    f"confidence={result['strategy']['confidence']} "
                    f"net_score={result['strategy']['net_score']}",
                    flush=True,
                )
        print(
            f"strategies stored: {len(stored_strategies)} "
            f"({', '.join(stored_strategies) or '-'})"
        )
        print(
            f"strategies rejected (insufficient data): {len(rejected_strategies)} "
            f"({', '.join(rejected_strategies) or '-'})"
        )


if __name__ == "__main__":
    asyncio.run(main())
