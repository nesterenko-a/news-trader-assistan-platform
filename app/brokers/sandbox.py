import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import delete, select

from app.brokers import t_invest
from app.db.models import BrokerAccount, BrokerPortfolioSnapshot, BrokerSandboxAction

MESSAGES = {
    "pending": "Действие выполняется",
    "success": "Действие выполнено",
    "failed": "Брокер отклонил действие. Проверьте токен, счёт и сумму",
    "unknown": "Результат неизвестен. Обновите счета и проверьте баланс перед новым действием",
}


def amount_value(value):
    text = str(value).strip().replace(",", ".")
    if not re.fullmatch(r"\d{1,16}(?:\.\d{1,2})?", text):
        raise HTTPException(422, "Введите положительную сумму в рублях с точностью до копейки")
    value = Decimal(text)
    if value <= 0:
        raise HTTPException(422, "Сумма должна быть больше нуля")
    return {"currency": "rub", "units": str(int(value)), "nano": int((value % 1) * 10**9)}


def action_view(row):
    status = row.status
    if status == "pending" and (datetime.now(timezone.utc) - row.started_at.replace(tzinfo=timezone.utc)).total_seconds() >= 120:
        status = "unknown"
    return {"id": row.id, "kind": row.kind, "status": status, "message": MESSAGES[status], "result": row.result, "error_code": row.error_code}


async def connection(session, user_id, lock=False):
    row = await t_invest.connection_for(session, user_id, environment="sandbox", lock=lock)
    if row is None or row.environment != "sandbox":
        raise HTTPException(409, "Сначала подключите Sandbox token в Настройках")
    return row


def broker_client(row, client):
    return client or t_invest.TInvestClient(t_invest.decrypt_token(row), environment="sandbox")


async def sync_rows(session, row, rows):
    existing = {item.external_id: item for item in (await session.scalars(select(BrokerAccount).where(BrokerAccount.connection_id == row.id))).all()}
    for item in rows:
        account = existing.pop(item["id"], None)
        if account is None:
            account = BrokerAccount(connection_id=row.id, external_id=item["id"])
            session.add(account)
        account.name = item.get("name") or "Тестовый счёт"
        account.account_type = item.get("type") or ""
        account.status = item.get("status") or ""
        account.access_level = item.get("accessLevel") or ""
    for account in existing.values():
        await session.execute(delete(BrokerPortfolioSnapshot).where(BrokerPortfolioSnapshot.account_id == account.id))
        if row.selected_account_id == account.external_id:
            row.selected_account_id, row.last_refreshed_at = None, None
        await session.delete(account)


async def list_accounts(session, user_id, client=None):
    row = await connection(session, user_id)
    broker = broker_client(row, client)
    error = None
    try:
        rows = await broker.accounts()
    except HTTPException:
        rows, error = [], "Не удалось получить счета. Проверьте подключение и повторите обновление"
    accounts = []
    for account in rows:
        balance = None
        try:
            positions = await broker.sandbox_positions(account["id"])
            rub = next((item for item in positions.get("money", []) if item.get("currency", "").lower() == "rub"), None)
            balance = t_invest.decimal_value(rub) if rub else None
        except HTTPException:
            pass
        accounts.append({"id": account["id"], "name": account.get("name") or "Тестовый счёт", "masked_id": "••••" + account["id"][-4:], "status": account.get("status", ""), "rub_balance": balance})
    actions = (await session.scalars(select(BrokerSandboxAction).where(BrokerSandboxAction.user_id == user_id).order_by(BrokerSandboxAction.started_at.desc()).limit(10))).all()
    return {"accounts": accounts, "actions": [action_view(item) for item in actions], "error": error}


async def sync(session, user_id, client=None):
    row = await connection(session, user_id, lock=True)
    await t_invest.ensure_available(session, row)
    rows = await broker_client(row, client).accounts()
    await sync_rows(session, row, rows)
    await session.commit()


async def execute(session, user_id, kind, operation_key, *, name="", account_id="", amount="", confirmed=False, client=None):
    try:
        operation_key = str(UUID(str(operation_key)))
    except ValueError as exc:
        raise HTTPException(422, "Неверный ключ операции") from exc
    if kind == "create":
        name = name.strip()
        if len(name) > 100:
            raise HTTPException(422, "Название — до 100 символов")
        method, payload = "OpenSandboxAccount", {"name": name} if name else {}
    elif kind == "pay_in":
        method, payload = "SandboxPayIn", {"accountId": account_id, "amount": amount_value(amount)}
    elif kind == "close" and confirmed is True:
        method, payload = "CloseSandboxAccount", {"accountId": account_id}
    else:
        raise HTTPException(422, "Подтвердите закрытие счёта")
    digest = hashlib.sha256(json.dumps({"kind": kind, "payload": payload}, sort_keys=True).encode()).hexdigest()
    row = await connection(session, user_id, lock=True)
    old = await session.scalar(select(BrokerSandboxAction).where(BrokerSandboxAction.user_id == user_id, BrokerSandboxAction.operation_key == operation_key))
    if old:
        if old.request_hash != digest:
            raise HTTPException(409, "Этот ключ уже использован для другого действия")
        await session.commit()
        return action_view(old)
    await t_invest.ensure_available(session, row)
    broker = broker_client(row, client)
    if kind != "create" and not any(item["id"] == account_id for item in await broker.accounts()):
        raise HTTPException(404, "Счёт недоступен")
    row.status = "sandbox_busy"
    row.version += 1
    action = BrokerSandboxAction(id=str(uuid4()), user_id=user_id, connection_id=row.id, connection_version=row.version, operation_key=operation_key, kind=kind, request_hash=digest, payload=payload, status="pending", result={}, started_at=datetime.now(timezone.utc))
    session.add(action)
    await session.commit()
    try:
        result = await broker.sandbox_change(method, payload)
        if kind == "create" and not result.get("accountId"):
            raise t_invest.SandboxUnknown()
        if kind == "pay_in" and not isinstance(result.get("balance"), dict):
            raise t_invest.SandboxUnknown()
        action.status = "success"
        action.result = {key: result[key] for key in ("accountId", "balance") if key in result}
    except t_invest.SandboxUnknown:
        action.status, action.error_code = "unknown", "transport_unknown"
    except HTTPException:
        action.status, action.error_code = "failed", "broker_rejected"
    action.finished_at = datetime.now(timezone.utc)
    await session.refresh(row, with_for_update=True)
    if row.version != action.connection_version:
        action.error_code = "connection_changed"
        t_invest._event(session, user_id, row.id, "sandbox_" + kind, action.status, action.error_code)
        await session.commit()
        return action_view(action)
    if action.status == "success":
        try:
            if kind in ("pay_in", "close"):
                account = await session.scalar(select(BrokerAccount).where(BrokerAccount.connection_id == row.id, BrokerAccount.external_id == account_id))
                if account:
                    await session.execute(delete(BrokerPortfolioSnapshot).where(BrokerPortfolioSnapshot.account_id == account.id))
                    if kind == "close":
                        await session.delete(account)
                if row.selected_account_id == account_id:
                    row.last_refreshed_at = None
                    if kind == "close":
                        row.selected_account_id = None
            await sync_rows(session, row, await broker.accounts())
        except HTTPException:
            action.error_code = "sync_failed"
    row.status = "connected"
    t_invest._event(session, user_id, row.id, "sandbox_" + kind, action.status, action.error_code)
    await session.commit()
    return action_view(action)
