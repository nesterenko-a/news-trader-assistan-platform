import argparse
import asyncio
from datetime import date, timedelta

from app.db.connection import SessionLocal, init_db
from app.market.moex import MOEXClient
from app.market.oi_data import sync_security_oi


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync futures open positions (OI) from MOEX ISS"
    )
    parser.add_argument(
        "--ticker",
        action="append",
        default=[],
        help="фьючерсный код SECID (например W4V6); можно несколько раз",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="скачать OI по всем фьючерсам, доступным на MOEX",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="окно в днях (по умолчанию 30; работает и с --ticker, и с --all)",
    )
    parser.add_argument(
        "--from",
        dest="from_date",
        default="",
        help="start date YYYY-MM-DD (полная история), overrides --days",
    )
    args = parser.parse_args()

    since = date.fromisoformat(args.from_date) if args.from_date else None

    await init_db()
    async with SessionLocal() as session:
        futures = await MOEXClient().fetch_futures_list()
        futures_meta = {
            f["secid"]: {"assetcode": f.get("assetcode"), "lastdeldate": f.get("lastdeldate")}
            for f in futures
        }
        tickers = [t.upper() for t in args.ticker]
        if args.all:
            tickers = [f["secid"] for f in futures]
            print(f"Найдено фьючерсов на MOEX: {len(tickers)}. ", flush=True)
        if not tickers:
            parser.error("укажите --ticker SECID или --all")

        if since is None:
            since = date.today() - timedelta(days=args.days or 30)

        print(
            f"Синхронизация открытых позиций MOEX ({len(tickers)} фьючерсов, "
            f"окно с {since.isoformat()})...",
            flush=True,
        )
        total = 0
        for i, ticker in enumerate(tickers, 1):
            inserted = await sync_security_oi(
                session, ticker, since=since, futures_meta=futures_meta
            )
            total += inserted
            print(f"  [{i}/{len(tickers)}] {ticker}: +{inserted} записей OI", flush=True)
        print(f"Итого обновлено: {total} записей OI")


if __name__ == "__main__":
    asyncio.run(main())
