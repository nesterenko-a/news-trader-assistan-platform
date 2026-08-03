import argparse
import asyncio

from app.alerts.delivery import deliver_telegram
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
        print("Генерация алертов по watchlist всех пользователей...", flush=True)
        created = await process_alerts(session, since=since)
        print(f"Алертов создано: {len(created)}", flush=True)
        telegram_sent = await deliver_telegram(session, created)
        print(f"Telegram: отправлено {telegram_sent} алертов", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
