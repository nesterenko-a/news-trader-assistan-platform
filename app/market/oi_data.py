from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MarketCandle, MarketOpenPosition, Security
from app.market.indicators.oi import calculate_oi
from app.market.moex import MOEXClient


async def ensure_futures_security(
    session: AsyncSession,
    ticker: str,
    shortname: str = "",
    assetcode: str | None = None,
    lastdeldate: date | str | None = None,
) -> Security:
    """Возвращает Security по тикеру; если фьючерса нет в справочнике — создаёт.

    assetcode — код базового актива (тикер акции), lastdeldate — дата экспирации
    (принимает date или строку ISO); заданные значения проставляются и при
    создании, и при обновлении существующей записи.
    """
    if isinstance(lastdeldate, str) and lastdeldate:
        try:
            lastdeldate = date.fromisoformat(lastdeldate)
        except ValueError:
            lastdeldate = None
    security = await session.scalar(select(Security).where(Security.ticker == ticker))
    if security is None:
        security = Security(
            ticker=ticker,
            name=shortname or ticker,
            market="MOEX",
            security_type="futures",
            sector="",
            currency="RUB",
            aliases=[],
            assetcode=assetcode,
            lastdeldate=lastdeldate,
        )
        session.add(security)
        await session.flush()
    else:
        changed = False
        if assetcode and security.assetcode != assetcode:
            security.assetcode = assetcode
            changed = True
        if lastdeldate and security.lastdeldate != lastdeldate:
            security.lastdeldate = lastdeldate
            changed = True
        if changed:
            await session.flush()
    return security


async def sync_security_oi(
    session: AsyncSession,
    ticker: str,
    days: int | None = None,
    since: date | None = None,
    futures_meta: dict[str, dict] | None = None,
) -> int:
    """Скачивает историю открытых позиций фьючерса с ISS и сохраняет в market_open_positions."""
    ticker = ticker.upper()

    if since is not None:
        from_date = since
    else:
        from_date = date.today() - timedelta(days=days or 30)

    rows = await MOEXClient().fetch_open_positions(
        ticker,
        from_date=from_date,
        till_date=date.today(),
    )
    if not rows:
        return 0

    meta = (futures_meta or {}).get(ticker, {})
    security = await ensure_futures_security(
        session,
        ticker,
        rows[-1]["shortname"],
        assetcode=meta.get("assetcode"),
        lastdeldate=meta.get("lastdeldate"),
    )

    inserted = 0
    for row in rows:
        if row["open_position"] > 0 or row["open_position_value"] is not None:
            existing = await session.scalar(
                select(MarketOpenPosition).where(
                    MarketOpenPosition.security_id == security.id,
                    MarketOpenPosition.trading_date == row["date"],
                )
            )
            if existing is None:
                session.add(
                    MarketOpenPosition(
                        security_id=security.id,
                        trading_date=row["date"],
                        open_position=row["open_position"],
                        open_position_value=row["open_position_value"],
                        source="iss",
                    )
                )
                inserted += 1

        if row["close"] is not None:
            candle = await session.scalar(
                select(MarketCandle).where(
                    MarketCandle.security_id == security.id,
                    MarketCandle.trading_date == row["date"],
                )
            )
            if candle is None:
                session.add(
                    MarketCandle(
                        security_id=security.id,
                        trading_date=row["date"],
                        open=row["open"],
                        high=row["high"],
                        low=row["low"],
                        close=row["close"],
                        volume=row["volume"],
                    )
                )

    await session.commit()
    return inserted


async def futures_for_security(
    session: AsyncSession, ticker: str
) -> list[Security]:
    """Фьючерсы, базовым активом которых является бумага (assetcode == ticker)."""
    ticker = ticker.upper()
    rows = await session.scalars(
        select(Security)
        .where(Security.security_type == "futures", Security.assetcode == ticker)
        .order_by(Security.lastdeldate)
    )
    return list(rows.all())


async def nearest_future(
    session: AsyncSession,
    ticker: str,
    as_of: date | None = None,
) -> Security | None:
    """Ближайший по экспирации фьючерс на бумагу.

    Сначала — фьючерсы с lastdeldate >= as_of (самый близкий к экспирации),
    если таких нет — последний по дате экспирации (или любой, если дат нет).
    """
    futures = await futures_for_security(session, ticker)
    if not futures:
        return None
    as_of = as_of or date.today()
    upcoming = [f for f in futures if f.lastdeldate and f.lastdeldate >= as_of]
    if upcoming:
        return min(upcoming, key=lambda f: f.lastdeldate)
    dated = [f for f in futures if f.lastdeldate]
    if dated:
        return max(dated, key=lambda f: f.lastdeldate)
    return futures[0]


async def latest_oi_signal(
    session: AsyncSession,
    security_id: int,
    as_of: date | None = None,
    lookback_rows: int = 90,
) -> dict | None:
    """Последний сигнал «цена × OI» по данным, доступным на as_of (без заглядывания в будущее).

    Возвращает None, если OI-данных нет или сигналов за окно не было.
    """
    as_of = as_of or date.today()
    oi_rows = (
        await session.scalars(
            select(MarketOpenPosition)
            .where(
                MarketOpenPosition.security_id == security_id,
                MarketOpenPosition.trading_date <= as_of,
            )
            .order_by(MarketOpenPosition.trading_date.desc())
            .limit(lookback_rows)
        )
    ).all()
    if not oi_rows:
        return None
    oi_rows.sort(key=lambda r: r.trading_date)
    min_date = oi_rows[0].trading_date
    max_date = oi_rows[-1].trading_date
    candles = (
        await session.scalars(
            select(MarketCandle)
            .where(
                MarketCandle.security_id == security_id,
                MarketCandle.trading_date >= min_date,
                MarketCandle.trading_date <= max_date,
            )
            .order_by(MarketCandle.trading_date)
        )
    ).all()
    close_by_date = {c.trading_date: c.close for c in candles}
    series = [
        (r.trading_date, close_by_date.get(r.trading_date), r.open_position)
        for r in oi_rows
    ]
    result = calculate_oi(series)
    if not result.signals:
        return None
    last = result.signals[-1]
    oi_by_date = {v.date: v.value for v in result.values if v.kind == "oi"}
    change_by_date = {
        v.date: v.value for v in result.values if v.kind == "oi_change_pct"
    }
    return {
        "date": last.date,
        "kind": last.kind,
        "severity": last.severity,
        "note": last.note,
        "oi": oi_by_date.get(last.date),
        "oi_change_pct": change_by_date.get(last.date),
    }
