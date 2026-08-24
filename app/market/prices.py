from datetime import date, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MarketCandle, Security
from app.market.moex import MOEXClient
from app.market.renames import actual_ticker


async def sync_security_prices(
    session: AsyncSession,
    ticker: str,
    days: int | None = None,
    since: date | None = None,
) -> int:
    security = await session.scalar(select(Security).where(Security.ticker == ticker))
    if security is None:
        return 0

    if since is not None:
        from_date = since
    else:
        from_date = date.today() - timedelta(days=days or 5)

    try:
        candles = await MOEXClient().fetch_candles(
            ticker,
            from_date=from_date,
            till_date=date.today(),
            security_type=security.security_type,
        )
    except httpx.HTTPError as exc:
        print(f"[prices] {ticker}: ошибка MOEX ({type(exc).__name__}) — пропускаю")
        return 0

    # Если по запрошенному тикеру MOEX не отдаёт данных (напр. тикер переименован:
    # FIVE → X5), пробуем актуальный биржевой тикер, сохраняя свечи к этой бумаге.
    if not candles:
        live = actual_ticker(ticker)
        if live != ticker.strip().upper():
            try:
                candles = await MOEXClient().fetch_candles(
                    live,
                    from_date=from_date,
                    till_date=date.today(),
                    security_type=security.security_type,
                )
                if candles:
                    print(f"[prices] {ticker}: данные получены по актуальному тикеру {live}")
                    # Обновляем тикер бумаги в БД на актуальный биржевой код
                    # (например FIVE → X5), если он ещё не занят другой бумагой.
                    clash = await session.scalar(
                        select(Security).where(
                            Security.ticker == live, Security.id != security.id
                        )
                    )
                    if clash is None:
                        security.ticker = live
                        await session.commit()
                        print(f"[prices] {ticker}: тикер в БД обновлён на {live}")
                    else:
                        print(
                            f"[prices] {ticker}: тикер {live} занят другой бумагой "
                            f"(id={clash.id}) — тикер в БД не меняем"
                        )
            except httpx.HTTPError as exc2:
                print(f"[prices] {ticker}: ошибка MOEX по {live} ({type(exc2).__name__})")
                candles = []

    inserted = 0
    for candle in candles:
        existing = await session.scalar(
            select(MarketCandle).where(
                MarketCandle.security_id == security.id,
                MarketCandle.trading_date == candle["date"],
            )
        )
        if existing is None:
            session.add(
                MarketCandle(
                    security_id=security.id,
                    trading_date=candle["date"],
                    open=candle["open"],
                    high=candle["high"],
                    low=candle["low"],
                    close=candle["close"],
                    volume=candle["volume"],
                )
            )
            inserted += 1

    await session.commit()
    return inserted
