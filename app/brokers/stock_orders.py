import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from app.brokers import mcp_stock_orders, sandbox, t_invest
from app.db.models import BrokerAccount, BrokerPortfolioSnapshot, BrokerSandboxAction, Security, UserFavorite

STATUSES = {
    "EXECUTION_REPORT_STATUS_NEW": "accepted",
    "EXECUTION_REPORT_STATUS_PARTIALLYFILL": "partial",
    "EXECUTION_REPORT_STATUS_FILL": "filled",
    "EXECUTION_REPORT_STATUS_REJECTED": "rejected",
    "EXECUTION_REPORT_STATUS_CANCELLED": "cancelled",
}
FINAL = {"filled", "rejected", "cancelled"}


def now():
    return datetime.now(timezone.utc)


async def require_account(client, account_id):
    if not any(account.get("id") == account_id and account.get("status") == "ACCOUNT_STATUS_OPEN"
               and account.get("accessLevel") == "ACCOUNT_ACCESS_LEVEL_FULL_ACCESS"
               for account in await client.accounts()):
        raise HTTPException(404, "Счёт недоступен для торговли")


async def context(session, user_id, account_id, *, lock=False):
    row = await t_invest.connection_for(session, user_id, environment="sandbox", lock=lock)
    if row is None:
        raise HTTPException(404, "Счёт песочницы недоступен")
    client = sandbox.broker_client(row, None)
    await require_account(client, account_id)
    return row, client


def supported(instrument, side):
    return bool(instrument and instrument.get("instrumentType") == "share"
                and str(instrument.get("currency", "")).lower() == "rub"
                and type(instrument.get("lot")) is int and instrument["lot"] > 0
                and instrument.get("apiTradeAvailableFlag") is True
                and instrument.get(f"{side}AvailableFlag") is True)


async def catalog(session, user_id, account_id):
    _, client = await context(session, user_id, account_id)
    favorite_tickers = await session.scalars(
        select(Security.ticker).join(UserFavorite, UserFavorite.security_id == Security.id)
        .where(UserFavorite.user_id == user_id, Security.security_type == "stock"),
    )
    favorites = {ticker.casefold() for ticker in favorite_tickers}
    result = {}
    for item in await client.shares():
        if not isinstance(item, dict) or not all(item.get(field) for field in ("uid", "ticker", "name")):
            continue
        if not supported({**item, "instrumentType": "share"}, "buy"):
            continue
        result[item["uid"]] = {
            "instrument_uid": item["uid"], "ticker": item["ticker"], "name": item["name"],
            "class_code": item.get("classCode") or "", "is_favorite": item["ticker"].casefold() in favorites,
        }
    return sorted(result.values(), key=lambda item: (item["ticker"], item["class_code"]))


def broker_integer(value):
    if type(value) is int or isinstance(value, str) and value.removeprefix("-").isascii() and value.removeprefix("-").isdigit():
        return int(value)
    raise ValueError("Invalid broker integer")


async def available_shares(client, account_id, uid):
    return position_shares(await client.sandbox_positions(account_id), uid)


def position_shares(positions, uid):
    try:
        rows = positions.get("securities", [])
        if not isinstance(rows, list):
            raise ValueError
        for position in rows:
            if position.get("instrumentUid") == uid:
                return 0 if position.get("exchangeBlocked") else max(0, broker_integer(position["balance"]))
    except (AttributeError, KeyError, ValueError) as exc:
        raise HTTPException(502, "Не удалось определить доступный остаток") from exc
    return 0


def rub_balance(positions):
    money = positions.get("money")
    if not isinstance(money, list):
        return None
    values = [t_invest.decimal_value(item) for item in money
              if isinstance(item, dict) and str(item.get("currency", "")).lower() == "rub"]
    value = values[0] if len(values) == 1 else None
    return value if value is not None and Decimal(value).is_finite() else None


async def draft_from_command(session, user_id, account_id, args):
    ticker, side, lots = args.get("ticker"), args.get("side"), args.get("lots")
    if (not account_id or not isinstance(ticker, str) or not 1 <= len(ticker) <= 32
            or side not in ("buy", "sell") or type(lots) is not int or not 1 <= lots <= 2**53 - 1):
        return None
    _, client = await context(session, user_id, account_id)
    matches = [item for item in await client.shares() if isinstance(item, dict)
               and str(item.get("ticker", "")).casefold() == ticker.strip().casefold()
               and item.get("uid") and supported({**item, "instrumentType": "share"}, side)]
    if len(matches) != 1:
        return None
    share = matches[0]
    return {"instrument_uid": share["uid"], "ticker": share["ticker"], "name": share.get("name") or share["ticker"],
            "side": side, "lots": lots}


async def details(session, user_id, account_id, uid):
    row, client = await context(session, user_id, account_id)
    instrument = await client.instrument(uid)
    buy, sell = supported(instrument, "buy"), supported(instrument, "sell")
    if not (buy or sell):
        raise HTTPException(422, "Акция недоступна для торговли")
    price, positions = await asyncio.gather(
        client.last_price(uid), client.sandbox_positions(account_id), return_exceptions=True,
    )
    try:
        shares = position_shares(positions, uid) if isinstance(positions, dict) else None
    except HTTPException:
        shares = None
    lot = instrument["lot"]
    shares = shares if type(shares) is int else None
    return {"lot": lot, "last_price": price if isinstance(price, str) else None,
            "available_shares": shares, "available_lots": shares // lot if shares is not None else None,
            "buy_available": buy, "sell_available": sell,
            "available_rub": rub_balance(positions) if isinstance(positions, dict) else None,
            "connection_version": row.version}


def view(action):
    result = action.result
    status = result.get("status", action.status)
    if status == "pending" and now() - action.started_at.replace(tzinfo=timezone.utc) >= timedelta(seconds=120):
        status = "unknown"
    transport = action.payload.get("transport", "rest")
    can_check = transport == "rest" or status == "pending" or bool(result.get("broker_id"))
    return {"operation_key": action.operation_key, "status": status, "lots_requested": action.payload["lots"],
            "lots_executed": result.get("lots_executed"), "portfolio_stale": result.get("portfolio_stale", False),
            "transport": transport, "can_check": can_check,
            "status_check_expired": now() - action.started_at.replace(tzinfo=timezone.utc) >= timedelta(days=7),
            "message": {"unknown": "Результат неизвестен. Проверьте статус этой заявки." if can_check else
                        "Ответ MCP не получен. Статус заявки неизвестен.",
                        "rejected": result.get("message", "Брокер отклонил заявку.")}.get(status)}


async def find_action(session, user_id, key):
    return await session.scalar(select(BrokerSandboxAction).where(
        BrokerSandboxAction.user_id == user_id, BrokerSandboxAction.operation_key == key,
    ).execution_options(populate_existing=True))


async def apply_report(session, row, action, client, report):
    broker_id = report.get("orderId")
    previous_id = action.result.get("broker_id")
    if isinstance(broker_id, str) and broker_id:
        if previous_id and broker_id != previous_id:
            return
        action.result = {**action.result, "broker_id": broker_id}
    broker_status = report.get("executionReportStatus")
    status = STATUSES.get(broker_status) if isinstance(broker_status, str) else None
    try:
        lots = broker_integer(report["lotsExecuted"])
        if not 0 <= lots <= action.payload["lots"]:
            raise ValueError
        if status == "filled" and lots != action.payload["lots"]:
            raise ValueError
    except (KeyError, ValueError):
        return
    if (status is None or lots < (action.result.get("lots_executed") or 0)
            or status == "accepted" and action.result.get("status") == "partial"):
        return
    refresh = lots > (action.result.get("lots_executed") or 0) or action.result.get("portfolio_stale", False)
    action.status = "failed" if status == "rejected" else "success"
    action.result = {"status": status, "lots_executed": lots,
                     "broker_id": action.result.get("broker_id"),
                     "portfolio_stale": refresh}
    if refresh:
        account = await session.scalar(select(BrokerAccount).where(
            BrokerAccount.connection_id == row.id, BrokerAccount.external_id == action.payload["account_id"],
        ))
        if account is None:
            return
        try:
            data = await t_invest._snapshot(session, client, account)
        except HTTPException:
            return
        snapshot = await session.scalar(select(BrokerPortfolioSnapshot).where(BrokerPortfolioSnapshot.account_id == account.id))
        if snapshot is None:
            snapshot = BrokerPortfolioSnapshot(account_id=account.id)
            session.add(snapshot)
        snapshot.data, snapshot.fetched_at = data, now()
        if row.selected_account_id == account.external_id:
            row.last_refreshed_at, row.last_error = snapshot.fetched_at, None
        action.result = {**action.result, "portfolio_stale": False}


async def submit(session, user_id, account_id, key, uid, side, lots, *, transport="rest", connection_version=None, last_price=None):
    key = str(key)
    payload = {"account_id": account_id, "instrument_uid": uid, "side": side, "lots": lots}
    if transport == "mcp":
        payload["transport"] = transport
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    row, client = await context(session, user_id, account_id, lock=True)
    existing = await find_action(session, user_id, key)
    if existing:
        if existing.kind != "stock_order" or existing.request_hash != digest or existing.connection_id != row.id:
            raise HTTPException(409, "Ключ уже использован для другого действия")
        return view(existing)
    await t_invest.ensure_available(session, row)
    if transport == "mcp" and connection_version != row.version:
        raise HTTPException(409, "Подключение изменилось. Откройте заявку заново")
    instrument = await client.instrument(uid)
    if not supported(instrument, side):
        raise HTTPException(422, "Акция недоступна для выбранного направления")
    positions = await client.sandbox_positions(account_id) if transport == "mcp" else None
    if transport == "mcp":
        price = Decimal(await client.last_price(uid) or "0")
        if not price.is_finite() or price <= 0 or not last_price or abs(price - last_price) > last_price / 100:
            raise HTTPException(409, "Цена изменилась или недоступна. Откройте заявку заново")
        funds = rub_balance(positions)
        if side == "buy" and (funds is None or Decimal(funds) < price * lots * instrument["lot"]):
            raise HTTPException(422, "Недостаточно свободных рублей или остаток недоступен")
    if side == "sell" and lots > (position_shares(positions, uid) if positions is not None else await available_shares(client, account_id, uid)) // instrument["lot"]:
        raise HTTPException(422, "Количество превышает доступный остаток")
    row.status = "sandbox_busy"
    row.version += 1
    action = BrokerSandboxAction(id=str(uuid4()), user_id=user_id, connection_id=row.id,
                                 connection_version=row.version, operation_key=key, kind="stock_order",
                                 request_hash=digest, payload=payload, status="pending", result={}, started_at=now())
    session.add(action)
    await session.commit()
    report = {}
    rejection = None
    try:
        if transport == "mcp":
            async with asyncio.timeout(60):
                async with mcp_stock_orders.SandboxOrderSession(row) as broker:
                    report = await broker.submit(account_id, uid, side, lots)
        if transport == "rest":
            report = await client.sandbox_change("PostSandboxOrder", {
                "accountId": account_id, "instrumentId": uid, "orderId": key,
                "direction": f"ORDER_DIRECTION_{side.upper()}", "orderType": "ORDER_TYPE_MARKET",
                "quantity": str(lots), "confirmMarginTrade": False,
            })
    except (t_invest.SandboxUnknown, TimeoutError):
        report = {}
    except HTTPException as exc:
        if transport == "rest":
            report = {"executionReportStatus": "EXECUTION_REPORT_STATUS_REJECTED", "lotsExecuted": "0"}
        if transport == "mcp" and exc.status_code == 422:
            report = {"executionReportStatus": "EXECUTION_REPORT_STATUS_REJECTED", "lotsExecuted": "0"}
            rejection = exc.detail
    await session.refresh(row, with_for_update=True)
    if row.version == action.connection_version:
        action.status = "unknown"
        action.finished_at = now()
        await apply_report(session, row, action, client, report)
        if rejection:
            action.result = {**action.result, "message": rejection}
        row.status = "connected"
        t_invest._event(session, user_id, row.id, "sandbox_stock_order", view(action)["status"])
        await session.commit()
    else:
        await session.refresh(action)
    return view(action)


async def status(session, user_id, account_id, key):
    row, client = await context(session, user_id, account_id, lock=True)
    action = await find_action(session, user_id, str(key))
    if (action is None or action.kind != "stock_order" or action.connection_id != row.id
            or action.payload["account_id"] != account_id):
        raise HTTPException(404, "Заявка не найдена")
    current = view(action)
    if current["status"] in FINAL | {"pending"} or current["status_check_expired"] or not current["can_check"]:
        return current
    await t_invest.ensure_available(session, row)
    exchange_id = action.result.get("broker_id")
    report = {}
    try:
        if current["transport"] == "mcp":
            async with mcp_stock_orders.SandboxOrderSession(row) as broker:
                report = await broker.status(account_id, exchange_id)
        if current["transport"] == "rest":
            report = await client.sandbox_order_state(account_id, exchange_id or action.operation_key,
                                                      "ORDER_ID_TYPE_EXCHANGE" if exchange_id else "ORDER_ID_TYPE_REQUEST")
    except HTTPException:
        return current
    await apply_report(session, row, action, client, report)
    await session.commit()
    return view(action)
