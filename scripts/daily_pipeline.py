import argparse
import asyncio
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select

from app.alerts.service import ALERT_LOOKBACK_DAYS, process_alerts
from app.alerts.delivery import deliver_telegram
from app.db.connection import SessionLocal, init_db
from app.db.models import Security
from app.market.prices import sync_security_prices
from app.paper.service import process_all_accounts
from app.strategy.engine import generate_strategy
from scripts.collect_news import (
    _parse_since,
    collect_news,
    collect_telegram_news,
    collect_website_news,
)

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
    parser.add_argument(
        "--sites",
        action="store_true",
        help="also collect news from company websites (kind='website')",
    )
    parser.add_argument(
        "--from-phase",
        type=int,
        default=1,
        help="start from pipeline phase N (1-5), skipping already successful phases",
    )
    parser.add_argument(
        "--tickers",
        default="",
        help="COM-разделённый список SECID фьючерсов для синхронизации в фазе 2 (из шаблона)",
    )
    args = parser.parse_args()

    since = _parse_since(args)
    if since is not None:
        print(f"Collecting news published since {since.isoformat()}")

    await init_db()

    phase_start = max(1, min(args.from_phase, 5))
    if phase_start > 1:
        print(f"Пропускаю фазы 1..{phase_start - 1} (запуск с фазы {phase_start}/5)", flush=True)

    async with SessionLocal() as session:
        print("Ежедневный конвейер запущен", flush=True)

        if phase_start <= 1:
            print("Фаза 1/5: сбор и анализ новостей...", flush=True)
            stored = await collect_news(session, since=since)
            print(f"Новости: {stored} сохранено", flush=True)

            if args.telegram:
                print("Фаза 1b/5: Telegram-каналы...", flush=True)
                tg_stored = await collect_telegram_news(session, since=since)
                print(f"Telegram-новости: {tg_stored} сохранено", flush=True)

            if args.sites:
                print("Фаза 1c/5: сайты компаний...", flush=True)
                site_stored = await collect_website_news(session, since=since)
                print(f"Сайты: {site_stored} сохранено", flush=True)

        securities = (
            await session.scalars(select(Security).order_by(Security.ticker))
        ).all()
        all_tickers = [
            (s.ticker, s.security_type)
            for s in securities
        ]

        if phase_start <= 2:
            print(f"Фаза 2/5: синхронизация свечей MOEX ({len(all_tickers)} бумаг)...", flush=True)
            synced = 0
            # Подзадача «Синхронизация акций»
            stock_tickers = [t for t, st in all_tickers if st != "futures"]
            for i, ticker in enumerate(stock_tickers, 1):
                synced += await sync_security_prices(session, ticker, PRICE_LOOKBACK_DAYS)
                print(f"  [акции {i}/{len(stock_tickers)}] {ticker}: свечи синхронизированы", flush=True)
            print(f"Синхронизация акций: {synced} свечей обновлено", flush=True)
            # Подзадача «Синхронизация фьючерсов» (по шаблону, если передан --tickers)
            template_tickers = [
                t.strip().upper() for t in args.tickers.split(",") if t.strip()
            ]
            future_tickers = [t for t, st in all_tickers if st == "futures"]
            if template_tickers:
                future_tickers = [
                    t for t in future_tickers if t in template_tickers
                ]
            if future_tickers:
                synced = 0
                for i, ticker in enumerate(future_tickers, 1):
                    synced += await sync_security_prices(session, ticker, PRICE_LOOKBACK_DAYS)
                    print(f"  [фьючерсы {i}/{len(future_tickers)}] {ticker}: свечи синхронизированы", flush=True)
                print(f"Синхронизация фьючерсов: {synced} свечей обновлено", flush=True)
            else:
                print("Синхронизация фьючерсов: пропущено (шаблон пуст)", flush=True)

        if phase_start <= 3:
            # Стратегии генерируются только для акций — фьючерсов нет в knowledge graph,
            # для них систематический INSUFFICIENT_DATA (см. docs/09 §3).
            strategy_tickers = [t for t, st in all_tickers if st != "futures"]
            print(f"Фаза 3/5: генерация стратегий ({len(strategy_tickers)} акций)...", flush=True)
            stored_strategies = []
            rejected_strategies = []
            for i, ticker in enumerate(strategy_tickers, 1):
                print(f"  [{i}/{len(strategy_tickers)}] {ticker}: анализ...", flush=True)
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

        if phase_start <= 4:
            print("Фаза 4/5: генерация алертов...", flush=True)
            created_alerts = await process_alerts(
                session,
                since=datetime.now(timezone.utc) - timedelta(days=ALERT_LOOKBACK_DAYS),
            )
            print(f"Алерты: {len(created_alerts)} создано", flush=True)
            telegram_sent = await deliver_telegram(session, created_alerts)
            print(f"Telegram: отправлено {telegram_sent} алертов", flush=True)

        if phase_start <= 5:
            print("Фаза 5/5: виртуальный портфель (paper trading)...", flush=True)
            paper_result = await process_all_accounts(session)
            print(
                f"Paper: открыто {paper_result['opened']}, закрыто {paper_result['closed']}",
                flush=True,
            )
        print("Конвейер завершён", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
