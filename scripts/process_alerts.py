import argparse
import asyncio

from app.alerts.service import process_alerts
from app.db.connection import SessionLocal, init_db


async def main() -> None:
    parser = argparse.ArgumentParser(description="Generate alerts from watched securities news")
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="lookback days for news to consider (0 = no limit)",
    )
    args = parser.parse_args()

    await init_db()
    since = None
    if args.days and args.days > 0:
        from datetime import datetime, timedelta, timezone

        since = datetime.now(timezone.utc) - timedelta(days=args.days)
    async with SessionLocal() as session:
        created = await process_alerts(session, since=since)
        print(f"alerts created: {created}")


if __name__ == "__main__":
    asyncio.run(main())
