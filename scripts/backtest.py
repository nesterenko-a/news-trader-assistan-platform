import asyncio
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.connection import SessionLocal, init_db
from app.db.models import MarketCandle, Security, Strategy

FORWARD_TRADING_DAYS = 5


async def _load_bars(session: AsyncSession, security_id: int) -> list[tuple[date, float]]:
    rows = await session.scalars(
        select(MarketCandle)
        .where(MarketCandle.security_id == security_id)
        .order_by(MarketCandle.trading_date)
    )
    return [(r.trading_date, r.close) for r in rows if r.close is not None]


def _entry_and_exit(
    bars: list[tuple[date, float]], generated_at: datetime
) -> tuple[float | None, float | None]:
    target = generated_at.date()
    if generated_at.tzinfo is not None:
        target = generated_at.astimezone(timezone.utc).date()

    entry_index = None
    for i, (bar_date, close) in enumerate(bars):
        if bar_date >= target:
            entry_index = i
            break
    if entry_index is None:
        return None, None

    exit_index = entry_index + FORWARD_TRADING_DAYS
    if exit_index >= len(bars):
        return None, None
    return bars[entry_index][1], bars[exit_index][1]


async def main() -> None:
    await init_db()
    async with SessionLocal() as session:
        securities = {
            s.id: s.ticker for s in (await session.scalars(select(Security))).all()
        }
        strategies = (
            await session.scalars(
                select(Strategy)
                .where(Strategy.verdict.in_(["BUY", "SELL"]))
                .order_by(Strategy.generated_at)
            )
        ).all()
        print(f"Бэктест сохранённых вердиктов: {len(strategies)} стратегий...", flush=True)

        evaluated = 0
        correct = 0
        summary = []
        by_verdict = {"BUY": [0, 0], "SELL": [0, 0]}

        for i, strategy in enumerate(strategies, 1):
            bars = await _load_bars(session, strategy.security_id)
            if not bars:
                continue
            entry, exit_price = _entry_and_exit(bars, strategy.generated_at)
            if entry is None or exit_price is None:
                continue

            forward_return = exit_price / entry - 1.0
            is_correct = (strategy.verdict == "BUY" and forward_return > 0) or (
                strategy.verdict == "SELL" and forward_return < 0
            )
            evaluated += 1
            correct += int(is_correct)
            by_verdict[strategy.verdict][0] += 1
            by_verdict[strategy.verdict][1] += int(is_correct)
            ticker = securities.get(strategy.security_id, str(strategy.security_id))
            summary.append(
                f"{ticker:5s} {strategy.verdict:4s} gen={strategy.generated_at.date()} "
                f"ret_{FORWARD_TRADING_DAYS}d={forward_return:+.2%} "
                f"{'OK' if is_correct else 'MISS'}"
            )
            if i % 20 == 0 or i == len(strategies):
                print(f"  обработано стратегий: {i}/{len(strategies)}", flush=True)

        if summary:
            print("\n".join(summary))
            print(
                f"\nEvaluated: {evaluated}, correct: {correct} "
                f"({correct / evaluated:.0%})"
            )
            for verdict, (total, hits) in by_verdict.items():
                if total:
                    print(f"  {verdict}: {hits}/{total} = {hits / total:.0%}")
        else:
            print(
                "No evaluable strategies yet: "
                "strategies accumulate daily via scheduler; "
                "evaluation needs close prices 5 trading days after generation."
            )


if __name__ == "__main__":
    asyncio.run(main())
