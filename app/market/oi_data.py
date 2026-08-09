from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    MarketCandle,
    MarketOpenPosition,
    MarketOpenPositionClientGroup,
    Security,
)
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
    client_groups_cache: dict | None = None,
) -> int:
    """Скачивает историю открытых позиций фьючерса с ISS и сохраняет в market_open_positions.

    Помимо общего OI сохраняет свечи цен и, если у фьючерса известен assetcode
    (код базового актива), — открытые позиции по группам клиентов (физ/юр лица)
    из сервиса OpenOptionService (см. docs/19 §8.14). client_groups_cache —
    общий кэш {(assetcode, date): dict} между вызовами (для --all не дублирует запросы).
    """
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

    cache = client_groups_cache if client_groups_cache is not None else {}
    assetcode = security.assetcode
    if assetcode:
        client = MOEXClient()
        for row in rows:
            key = (assetcode, row["date"])
            data = cache.get(key)
            if data is None:
                try:
                    data = await client.fetch_open_positions_client_groups(
                        assetcode, row["date"]
                    )
                except Exception:
                    data = None
                cache[key] = data
            if data is None:
                continue
            for group in ("physical", "juridical"):
                existing = await session.scalar(
                    select(MarketOpenPositionClientGroup).where(
                        MarketOpenPositionClientGroup.security_id == security.id,
                        MarketOpenPositionClientGroup.trading_date == row["date"],
                        MarketOpenPositionClientGroup.client_group == group,
                    )
                )
                long_pos = data.get(f"{group}_long") or 0
                short_pos = data.get(f"{group}_short") or 0
                if existing is None:
                    session.add(
                        MarketOpenPositionClientGroup(
                            security_id=security.id,
                            trading_date=row["date"],
                            client_group=group,
                            long_pos=long_pos,
                            short_pos=short_pos,
                            net_pos=long_pos - short_pos,
                            participants=data.get(f"{group}_participants") or 0,
                            summary=data.get("summary") or 0,
                        )
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


async def client_groups_series(
    session: AsyncSession,
    security_id: int,
    from_date: date | None = None,
    till_date: date | None = None,
) -> list[dict]:
    """Ряды открытых позиций по группам клиентов (физ/юр) + метрики.

    Возвращает список по датам: long/short/net групп, сумма, доля физиков (%),
    спред нетто «юр − физ» (см. docs/19 §8.14).
    """
    q = select(MarketOpenPositionClientGroup).where(
        MarketOpenPositionClientGroup.security_id == security_id
    )
    if from_date is not None:
        q = q.where(MarketOpenPositionClientGroup.trading_date >= from_date)
    if till_date is not None:
        q = q.where(MarketOpenPositionClientGroup.trading_date <= till_date)
    q = q.order_by(MarketOpenPositionClientGroup.trading_date)
    rows = (await session.scalars(q)).all()

    by_date: dict[date, dict] = {}
    for r in rows:
        d = by_date.setdefault(r.trading_date, {})
        d[r.client_group] = {
            "long": r.long_pos,
            "short": r.short_pos,
            "net": r.net_pos,
            "participants": r.participants,
        }
        d["summary"] = r.summary

    out = []
    for d, groups in sorted(by_date.items()):
        ph = groups.get("physical") or {"long": 0, "short": 0, "net": 0}
        ju = groups.get("juridical") or {"long": 0, "short": 0, "net": 0}
        summary = groups.get("summary") or 0
        share = round(ph["long"] * 100.0 / summary, 1) if summary else None
        out.append(
            {
                "date": d,
                "physical": ph,
                "juridical": ju,
                "summary": summary,
                "physical_share_pct": share,
                "net_spread": ju["net"] - ph["net"],
            }
        )
    return out


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
    volume_by_date = {c.trading_date: c.volume for c in candles}
    series = [
        (
            r.trading_date,
            close_by_date.get(r.trading_date),
            r.open_position,
            volume_by_date.get(r.trading_date),
        )
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
        "volume": last.volume,
        "oi": oi_by_date.get(last.date),
        "oi_change_pct": change_by_date.get(last.date),
    }
