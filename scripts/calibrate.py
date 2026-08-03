import asyncio
import statistics

from sqlalchemy import select

from app.db.connection import SessionLocal, init_db
from app.db.models import Security
from app.strategy.engine import BUY_THRESHOLD, SELL_THRESHOLD, generate_strategy


async def main() -> None:
    await init_db()
    async with SessionLocal() as session:
        securities = (
            await session.scalars(select(Security).order_by(Security.ticker))
        ).all()

        print(f"Калибровка: анализ {len(securities)} бумаг (без сохранения)...", flush=True)
        rows = []
        for i, security in enumerate(securities, 1):
            print(f"  [{i}/{len(securities)}] {security.ticker}: скоринг...", flush=True)
            result = await generate_strategy(session, security.ticker, persist=False)
            strategy = result["strategy"]
            rows.append(
                {
                    "ticker": security.ticker,
                    "verdict": strategy["verdict"],
                    "score": strategy["net_score"],
                    "confidence": strategy["confidence"],
                    "signals": len(result["signals"]),
                }
            )
            print(
                f"    {security.ticker:5s} {strategy['verdict']:18s} "
                f"score={strategy['net_score']:+.3f} "
                f"conf={strategy['confidence']:.2f} "
                f"signals={len(result['signals'])}",
                flush=True,
            )

        scored = [r for r in rows if r["verdict"] != "INSUFFICIENT_DATA"]
        if scored:
            values = [r["score"] for r in scored]
            values_sorted = sorted(values)
            n = len(values)
            print("\nDistribution of non-trivial scores:")
            print(f"  n={n}")
            print(f"  min={values_sorted[0]:+.3f} max={values_sorted[-1]:+.3f}")
            print(f"  mean={statistics.mean(values):+.3f} stdev={statistics.stdev(values):.3f}")
            q25 = values_sorted[int(n * 0.25)]
            q50 = values_sorted[int(n * 0.5)]
            q75 = values_sorted[int(n * 0.75)]
            print(f"  q25={q25:+.3f} q50={q50:+.3f} q75={q75:+.3f}")

        counts = {}
        for row in rows:
            counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
        print(f"\nVerdict counts: {counts}")
        print(f"Current thresholds: BUY > {BUY_THRESHOLD}, SELL < {SELL_THRESHOLD}")


if __name__ == "__main__":
    asyncio.run(main())
