import argparse
import asyncio
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select

from app.alerts.service import ALERT_LOOKBACK_DAYS, process_alerts
from app.alerts.delivery import deliver_telegram
from app.db.connection import SessionLocal, init_db
from app.db.models import Security
from app.market.prices import sync_security_prices
from app.strategy.engine import generate_strategy
from scripts.collect_news import _parse_since, collect_news, collect_telegram_news

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
    parser.add_argument(
        "--telegram",
        action="store_true",
        help="also collect news from Telegram channels (TELEGRAM_CHANNELS)",
    )
    args = parser.parse_args()

    since = _parse_since(args)
    if since is not None:
        print(f"Collecting news published since {since.isoformat()}")

    await init_db()
    async with SessionLocal() as session:
        print("Ежедневный конвейер запущен", flush=True)
        print("Фаза 1/4: сбор и анализ новостей...", flush=True)
        stored = await collect_news(session, since=since)
        print(f"Новости: {stored} сохранено", flush=True)

        if args.telegram:
            print("Фаза 1b/4: Telegram-каналы...", flush=True)
            tg_stored = await collect_telegram_news(session, since=since)
            print(f"Telegram-новости: {tg_stored} сохранено", flush=True)

        tickers = [
            s.ticker
            for s in (
                await session.scalars(select(Security).order_by(Security.ticker))
            ).all()
        ]

        print(f"Фаза 2/4: синхронизация свечей MOEX ({len(tickers)} бумаг)...", flush=True)
        synced = 0
        for i, ticker in enumerate(tickers, 1):
            synced += await sync_security_prices(session, ticker, PRICE_LOOKBACK_DAYS)
            print(f"  [{i}/{len(tickers)}] {ticker}: свечи синхронизированы", flush=True)
        print(f"Цены: {synced} свечей обновлено", flush=True)

        print("Фаза 3/4: генерация стратегий...", flush=True)
        stored_strategies = []
        rejected_strategies = []
        for i, ticker in enumerate(tickers, 1):
            print(f"  [{i}/{len(tickers)}] {ticker}: анализ...", flush=True)
            result = await generate_strategy(session, ticker)
            verdict = result["strategy"]["verdict"]
            if verdict == "INSUFFICIENT_DATA":
                rejected_strategies.append(ticker)
                print(
                    f"    {ticker}: REJECTED (insufficient data)",
                    flush=True,
                )
            else:
                stored_strategies.append(ticker)
                print(
                    f"    {ticker}: STORED "
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

        print("Фаза 4/4: генерация алертов...", flush=True)
        created_alerts = await process_alerts(
            session,
            since=datetime.now(timezone.utc) - timedelta(days=ALERT_LOOKBACK_DAYS),
        )
        print(f"Алерты: {len(created_alerts)} создано", flush=True)
        telegram_sent = await deliver_telegram(session, created_alerts)
        print(f"Telegram: отправлено {telegram_sent} алертов", flush=True)
        print("Конвейер завершён", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
