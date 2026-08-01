import argparse
import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.connection import SessionLocal, init_db
from app.db.models import MarketCandle, Security
from app.strategy.engine import generate_strategy

DEFAULT_HORIZON = 5
DEFAULT_PERIOD_DAYS = 180
AS_OF_TIME = time(18, 0)


@dataclass
class VerdictResult:
    ticker: str
    as_of: date
    verdict: str
    forward_return: float | None
    correct: bool | None


async def _load_bars(session: AsyncSession, security_id: int) -> list[tuple[date, float]]:
    rows = await session.scalars(
        select(MarketCandle)
        .where(MarketCandle.security_id == security_id)
        .order_by(MarketCandle.trading_date)
    )
    return [(r.trading_date, r.close) for r in rows if r.close is not None]


def evaluate(verdict: str, entry: float | None, exit_price: float | None) -> tuple[float | None, bool | None]:
    if entry is None or exit_price is None:
        return None, None
    ret = exit_price / entry - 1.0
    if verdict == "BUY":
        return ret, ret > 0
    if verdict == "SELL":
        return ret, ret < 0
    return ret, None


async def backtest_ticker(
    session: AsyncSession,
    ticker: str,
    start: date,
    end: date,
    horizon: int = DEFAULT_HORIZON,
    step_days: int = 1,
) -> list[VerdictResult]:
    security = await session.scalar(select(Security).where(Security.ticker == ticker))
    if security is None:
        return []
    bars = await _load_bars(session, security.id)
    results: list[VerdictResult] = []
    for i, (bar_date, close) in enumerate(bars):
        if bar_date < start or bar_date > end:
            continue
        if i % step_days != 0:
            continue
        exit_index = i + horizon
        if exit_index >= len(bars):
            continue
        as_of = datetime.combine(bar_date, AS_OF_TIME, tzinfo=timezone.utc)
        result = await generate_strategy(
            session, ticker, as_of=as_of, persist=False, use_live_market=False
        )
        verdict = result["strategy"]["verdict"]
        if verdict == "INSUFFICIENT_DATA":
            continue
        ret, correct = evaluate(verdict, close, bars[exit_index][1])
        results.append(
            VerdictResult(
                ticker=ticker,
                as_of=bar_date,
                verdict=verdict,
                forward_return=ret,
                correct=correct,
            )
        )
    return results


def build_report(results: list[VerdictResult], horizon: int) -> list[str]:
    evaluable = [r for r in results if r.correct is not None]
    lines = []
    for r in sorted(results, key=lambda r: (r.ticker, r.as_of)):
        mark = "OK" if r.correct is True else ("MISS" if r.correct is False else "-")
        ret_str = f"{r.forward_return:+.2%}" if r.forward_return is not None else "-"
        lines.append(f"{r.ticker:6s} {r.as_of} {r.verdict:4s} ret={ret_str} {mark}")

    lines.append("")
    total = len(evaluable)
    if total == 0:
        lines.append("Нет оцениваемых вердиктов за период.")
        return lines

    hits = sum(1 for r in evaluable if r.correct)
    avg = sum(r.forward_return for r in evaluable if r.forward_return is not None) / total
    lines.append(f"Оценено: {total} · Успех: {hits} ({hits / total:.0%})")
    lines.append(f"Средняя доходность за {horizon} торговых дней: {avg:+.2%}")

    lines.append("")
    lines.append("По вердиктам:")
    by_verdict: dict[str, list[VerdictResult]] = {}
    for r in evaluable:
        by_verdict.setdefault(r.verdict, []).append(r)
    for verdict, items in sorted(by_verdict.items()):
        v_hits = sum(1 for r in items if r.correct)
        v_avg = sum(r.forward_return for r in items if r.forward_return is not None) / len(items)
        lines.append(
            f"  {verdict}: {v_hits}/{len(items)} = {v_hits / len(items):.0%} · avg {v_avg:+.2%}"
        )

    lines.append("")
    lines.append("По периодам (месяц):")
    by_period: dict[str, list[VerdictResult]] = {}
    for r in evaluable:
        by_period.setdefault(r.as_of.strftime("%Y-%m"), []).append(r)
    for period, items in sorted(by_period.items()):
        p_hits = sum(1 for r in items if r.correct)
        p_avg = sum(r.forward_return for r in items if r.forward_return is not None) / len(items)
        lines.append(
            f"  {period}: {len(items)} оценок · {p_hits} OK ({p_hits / len(items):.0%}) · avg {p_avg:+.2%}"
        )
    return lines


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backtest: reconstruct verdicts 'as of time T' and measure forward quality"
    )
    parser.add_argument("--tickers", action="append", default=[])
    parser.add_argument("--start", default="", help="start date YYYY-MM-DD")
    parser.add_argument("--end", default="", help="end date YYYY-MM-DD")
    parser.add_argument("--horizon", type=int, default=DEFAULT_HORIZON)
    parser.add_argument(
        "--step",
        type=int,
        default=1,
        help="sample every Nth trading day (1 = every day)",
    )
    args = parser.parse_args()

    await init_db()
    async with SessionLocal() as session:
        if args.tickers:
            tickers = [t.upper() for t in args.tickers]
        else:
            tickers = [
                s.ticker
                for s in (
                    await session.scalars(select(Security).order_by(Security.ticker))
                ).all()
            ]
        end = date.fromisoformat(args.end) if args.end else date.today()
        start = date.fromisoformat(args.start) if args.start else end - timedelta(days=DEFAULT_PERIOD_DAYS)

        print(f"Бэктест «на момент T»: {', '.join(tickers)} · {start} .. {end} · горизонт {args.horizon} торговых дней")
        all_results: list[VerdictResult] = []
        for ticker in tickers:
            results = await backtest_ticker(
                session, ticker, start, end, horizon=args.horizon, step_days=args.step
            )
            all_results.extend(results)

        lines = build_report(all_results, args.horizon)
        print("\n".join(lines))
        if not all_results:
            print(
                "Подсказка: нужны новости и свечи за период; каждый вердикт оценивается "
                f"через {args.horizon} торговых дней после даты T."
            )


if __name__ == "__main__":
    asyncio.run(main())
