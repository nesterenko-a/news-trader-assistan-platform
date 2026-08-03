from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    MarketCandle,
    PaperAccount,
    PaperPosition,
    PaperTrade,
    Security,
    Strategy,
)

DEFAULT_CAPITAL = 1_000_000.0


async def get_or_create_account(session: AsyncSession, user_id: int) -> PaperAccount:
    account = await session.scalar(
        select(PaperAccount).where(PaperAccount.user_id == user_id)
    )
    if account is None:
        account = PaperAccount(user_id=user_id, initial_capital=DEFAULT_CAPITAL)
        session.add(account)
        await session.commit()
    return account


async def latest_closes(
    session: AsyncSession, security_ids: list[int]
) -> dict[int, tuple[object, float]]:
    result: dict[int, tuple[object, float]] = {}
    for security_id in security_ids:
        row = await session.scalar(
            select(MarketCandle)
            .where(MarketCandle.security_id == security_id)
            .order_by(MarketCandle.trading_date.desc())
            .limit(1)
        )
        if row is not None and row.close is not None:
            result[security_id] = (row.trading_date, row.close)
    return result


async def _latest_strategies(session: AsyncSession) -> dict[int, Strategy]:
    securities = (await session.scalars(select(Security))).all()
    latest: dict[int, Strategy] = {}
    for security in securities:
        strategy = await session.scalar(
            select(Strategy)
            .where(Strategy.security_id == security.id)
            .order_by(Strategy.generated_at.desc(), Strategy.id.desc())
            .limit(1)
        )
        if strategy is not None:
            latest[security.id] = strategy
    return latest


async def process_signals(session: AsyncSession, account: PaperAccount) -> dict:
    acted: set[int] = set(
        (
            await session.scalars(
                select(PaperTrade.strategy_id).where(
                    PaperTrade.account_id == account.id
                )
            )
        ).all()
    )
    latest = {
        sec_id: s
        for sec_id, s in (await _latest_strategies(session)).items()
        if s.id not in acted
    }
    closes = await latest_closes(session, list(latest.keys()))

    open_positions = {
        p.security_id: p
        for p in (
            await session.scalars(
                select(PaperPosition).where(
                    PaperPosition.account_id == account.id,
                    PaperPosition.status == "open",
                )
            )
        ).all()
    }

    buy_signals = [
        sec_id
        for sec_id, s in latest.items()
        if s.verdict == "BUY" and sec_id in closes
    ]
    per_position = account.initial_capital / len(buy_signals) if buy_signals else 0.0

    opened = 0
    closed = 0
    now = datetime.now(timezone.utc)
    for sec_id, strategy in latest.items():
        close_info = closes.get(sec_id)
        if close_info is None:
            continue
        close_date, price = close_info
        generated = strategy.generated_at
        if generated.tzinfo is not None:
            generated = generated.astimezone(timezone.utc)
        if generated.date() > close_date:
            continue

        if strategy.verdict == "BUY":
            if sec_id in open_positions or price <= 0:
                continue
            quantity = max(int(per_position / price), 1)
            session.add(
                PaperPosition(
                    account_id=account.id,
                    security_id=sec_id,
                    quantity=quantity,
                    entry_price=price,
                    entry_strategy_id=strategy.id,
                )
            )
            session.add(
                PaperTrade(
                    account_id=account.id,
                    security_id=sec_id,
                    side="open",
                    quantity=quantity,
                    price=price,
                    strategy_id=strategy.id,
                )
            )
            opened += 1
        elif strategy.verdict == "SELL":
            position = open_positions.get(sec_id)
            if position is None or price <= 0:
                continue
            position.status = "closed"
            position.closed_at = now
            position.exit_price = price
            position.realized_pnl = (price - position.entry_price) * position.quantity
            session.add(
                PaperTrade(
                    account_id=account.id,
                    security_id=sec_id,
                    side="close",
                    quantity=position.quantity,
                    price=price,
                    strategy_id=strategy.id,
                )
            )
            closed += 1

    await session.commit()
    return {"opened": opened, "closed": closed}


async def close_position(
    session: AsyncSession, account: PaperAccount, security_id: int, price: float
) -> bool:
    position = await session.scalar(
        select(PaperPosition).where(
            PaperPosition.account_id == account.id,
            PaperPosition.security_id == security_id,
            PaperPosition.status == "open",
        )
    )
    if position is None or price <= 0:
        return False
    position.status = "closed"
    position.closed_at = datetime.now(timezone.utc)
    position.exit_price = price
    position.realized_pnl = (price - position.entry_price) * position.quantity
    session.add(
        PaperTrade(
            account_id=account.id,
            security_id=security_id,
            side="close",
            quantity=position.quantity,
            price=price,
            strategy_id=None,
        )
    )
    await session.commit()
    return True


async def reset_account(session: AsyncSession, account: PaperAccount) -> None:
    positions = (
        await session.scalars(
            select(PaperPosition).where(PaperPosition.account_id == account.id)
        )
    ).all()
    trades = (
        await session.scalars(
            select(PaperTrade).where(PaperTrade.account_id == account.id)
        )
    ).all()
    for obj in [*positions, *trades]:
        await session.delete(obj)
    await session.commit()


async def _security_return(
    session: AsyncSession, security_id: int, start_date: object
) -> float | None:
    closes = (
        await session.scalars(
            select(MarketCandle.close)
            .where(
                MarketCandle.security_id == security_id,
                MarketCandle.trading_date >= start_date,
            )
            .order_by(MarketCandle.trading_date)
        )
    ).all()
    closes = [c for c in closes if c is not None]
    if len(closes) < 2:
        return None
    return closes[-1] / closes[0] - 1.0


async def benchmark_return(
    session: AsyncSession, account: PaperAccount
) -> float | None:
    start = account.created_at
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    start_date = start.date()
    security_ids = set(
        (
            await session.scalars(
                select(PaperTrade.security_id).where(
                    PaperTrade.account_id == account.id
                )
            )
        ).all()
    )
    returns = []
    for security_id in security_ids:
        value = await _security_return(session, security_id, start_date)
        if value is not None:
            returns.append(value)
    if not returns:
        return None
    return sum(returns) / len(returns)


async def account_view(session: AsyncSession, account: PaperAccount) -> dict:
    positions = (
        await session.scalars(
            select(PaperPosition)
            .where(
                PaperPosition.account_id == account.id,
                PaperPosition.status == "open",
            )
            .order_by(PaperPosition.opened_at)
        )
    ).all()
    trades = (
        await session.scalars(
            select(PaperTrade)
            .where(PaperTrade.account_id == account.id)
            .order_by(PaperTrade.ts)
        )
    ).all()
    closed = (
        await session.scalars(
            select(PaperPosition).where(
                PaperPosition.account_id == account.id,
                PaperPosition.status == "closed",
            )
        )
    ).all()
    securities = {
        s.id: s
        for s in (
            await session.scalars(select(Security).where(Security.id.in_(
                {p.security_id for p in positions} | {t.security_id for t in trades}
            )))
        ).all()
    }

    closes = await latest_closes(session, [p.security_id for p in positions])
    unrealized = 0.0
    position_items = []
    for position in positions:
        current = closes.get(position.security_id, (None, position.entry_price))[1]
        market_value = current * position.quantity
        pnl = (current - position.entry_price) * position.quantity
        unrealized += pnl
        security = securities.get(position.security_id)
        position_items.append(
            {
                "id": position.id,
                "ticker": security.ticker if security else "",
                "name": security.name if security else "",
                "quantity": position.quantity,
                "entry_price": position.entry_price,
                "current_price": current,
                "market_value": market_value,
                "pnl": pnl,
                "pnl_percent": (current / position.entry_price - 1.0) * 100
                if position.entry_price
                else None,
                "opened_at": position.opened_at,
            }
        )

    realized = sum(p.realized_pnl or 0.0 for p in closed)
    wins = sum(1 for p in closed if (p.realized_pnl or 0) > 0)
    total_pnl = realized + unrealized
    equity = account.initial_capital + total_pnl

    trade_items = []
    for trade in trades:
        security = securities.get(trade.security_id)
        trade_items.append(
            {
                "id": trade.id,
                "ticker": security.ticker if security else "",
                "side": trade.side,
                "quantity": trade.quantity,
                "price": trade.price,
                "strategy_id": trade.strategy_id,
                "ts": trade.ts,
            }
        )

    benchmark = await benchmark_return(session, account)

    drawdown = None
    if closed:
        running = 0.0
        peak = 0.0
        max_dd = 0.0
        for position in sorted(closed, key=lambda p: p.closed_at or p.opened_at):
            running += position.realized_pnl or 0.0
            peak = max(peak, running)
            max_dd = min(max_dd, running - peak)
        drawdown = round(max_dd, 2) if max_dd < 0 else 0.0

    metrics = {
        "initial_capital": account.initial_capital,
        "equity": round(equity, 2),
        "total_pnl": round(total_pnl, 2),
        "return_percent": round(total_pnl / account.initial_capital * 100, 2)
        if account.initial_capital
        else None,
        "realized": round(realized, 2),
        "unrealized": round(unrealized, 2),
        "wins": wins,
        "total_closed": len(closed),
        "win_rate": round(wins / len(closed) * 100, 1) if closed else None,
        "avg_result": round(sum(p.realized_pnl or 0.0 for p in closed) / len(closed), 2)
        if closed
        else None,
        "max_drawdown": drawdown,
        "benchmark_return": round(benchmark * 100, 2) if benchmark is not None else None,
    }
    return {
        "account_id": account.id,
        "currency": account.currency,
        "metrics": metrics,
        "positions": position_items,
        "trades": list(reversed(trade_items)),
    }


async def process_all_accounts(session: AsyncSession) -> dict:
    accounts = (await session.scalars(select(PaperAccount))).all()
    total = {"opened": 0, "closed": 0}
    for account in accounts:
        result = await process_signals(session, account)
        total["opened"] += result["opened"]
        total["closed"] += result["closed"]
    return total
