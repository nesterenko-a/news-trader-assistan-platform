import asyncio
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.db.connection import SessionLocal, init_db
from app.db.models import Article, ArticleEntity, Security
from app.graph.service import security_entity_ids
from app.market.moex import MOEXClient
from app.strategy.engine import generate_strategy

FORWARD_TRADING_DAYS = 5
MAX_AS_OF_DATES = 3


def _bar_on_or_before(bars: list[tuple[date, float]], target: date) -> float | None:
    for bar_date, close in bars:
        if bar_date <= target:
            return close
    return None


def _bar_at_offset(bars: list[tuple[date, float]], target: date, offset: int) -> float | None:
    base_index = None
    for i, (bar_date, _) in enumerate(bars):
        if bar_date <= target:
            base_index = i
        else:
            break
    if base_index is None:
        return None
    target_index = base_index + offset
    if target_index >= len(bars):
        return None
    return bars[target_index][1]


async def main() -> None:
    await init_db()
    moex = MOEXClient()

    async with SessionLocal() as session:
        securities = (
            await session.scalars(select(Security).order_by(Security.ticker))
        ).all()

        evaluated = 0
        correct = 0
        summary = []

        for security in securities:
            target_ids = await security_entity_ids(session, security.id)
            if not target_ids:
                continue

            bars = await moex.fetch_daily_bars(security.ticker, days=180)
            if not bars:
                continue

            articles = (
                await session.scalars(
                    select(ArticleEntity)
                    .where(ArticleEntity.entity_id.in_(target_ids))
                    .order_by(ArticleEntity.article_id.desc())
                )
            ).all()
            article_ids = list({ae.article_id for ae in articles})
            if not article_ids:
                continue

            article_dates = (
                await session.scalars(
                    select(Article.published_at)
                    .where(Article.id.in_(article_ids))
                    .order_by(Article.published_at.desc())
                )
            ).all()
            as_of_dates = sorted({d.date() for d in article_dates})[-MAX_AS_OF_DATES:]

            for as_of_date in as_of_dates:
                as_of = datetime.combine(as_of_date, datetime.min.time(), tzinfo=timezone.utc)
                result = await generate_strategy(session, security.ticker, as_of=as_of)
                verdict = result["strategy"]["verdict"]
                if verdict not in ("BUY", "SELL"):
                    continue

                entry = _bar_on_or_before(bars, as_of_date)
                exit_price = _bar_at_offset(bars, as_of_date, FORWARD_TRADING_DAYS)
                if entry is None or exit_price is None:
                    continue

                forward_return = exit_price / entry - 1.0
                is_correct = (verdict == "BUY" and forward_return > 0) or (
                    verdict == "SELL" and forward_return < 0
                )
                evaluated += 1
                correct += int(is_correct)
                summary.append(
                    f"{security.ticker:5s} {verdict:4s} as_of={as_of_date} "
                    f"ret_5d={forward_return:+.2%} {'OK' if is_correct else 'MISS'}"
                )

        if summary:
            print("\n".join(summary))
            print(f"\nEvaluated: {evaluated}, correct: {correct} "
                  f"({correct / evaluated:.0%} if evaluated > 0)")
        else:
            print("No evaluable samples yet: need historical news with forward prices.")


if __name__ == "__main__":
    asyncio.run(main())
