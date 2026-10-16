import argparse
import asyncio

from app.db.connection import SessionLocal
from app.tech_analysis.outcome_worker import evaluate_pending
from app.tech_analysis.outcome_alerts import deliver_pending, queue_deliveries


async def main():
    parser = argparse.ArgumentParser(description="Проверка исходов сценариев Top-5")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if args.interval < 30 or not 1 <= args.limit <= 1000:
        parser.error("Интервал — от 30 секунд, лимит — 1–1000")
    while True:
        try:
            async with SessionLocal() as session:
                count = await evaluate_pending(session, limit=args.limit)
                await queue_deliveries(session)
                await deliver_pending(session)
            print(f"Проверено сценариев: {count}", flush=True)
        except Exception as exc:
            print(f"Проход не завершён: {type(exc).__name__}", flush=True)
            if not args.loop:
                raise
        if not args.loop:
            break
        await asyncio.sleep(args.interval)


if __name__ == "__main__":
    asyncio.run(main())
