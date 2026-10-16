from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select

from app.db.models import ScenarioDelivery, ScenarioEvent, ScenarioSnapshot, ScenarioSubscription, User
from app.presentation.scenario import scenario_message
from app.tech_analysis.outcomes import owned


async def set_subscription(session, user, snapshot_id, channels, kinds):
    snapshot = await owned(session, snapshot_id, user.id)
    if snapshot is None:
        raise HTTPException(404, "Сценарий не найден")
    if channels and not snapshot.eligible:
        raise HTTPException(409, "Сценарий требует ручной проверки")
    if "telegram" in channels and not user.telegram_chat_id:
        raise HTTPException(409, "Сначала привяжите Telegram в настройках")
    row = await session.scalar(select(ScenarioSubscription).where(ScenarioSubscription.user_id == user.id, ScenarioSubscription.snapshot_id == snapshot_id).with_for_update())
    if row is None:
        row = ScenarioSubscription(user_id=user.id, snapshot_id=snapshot_id)
        session.add(row)
    if not row.active or row.id is None:
        row.created_at = datetime.now(timezone.utc)
        row.after_event_id = await session.scalar(select(func.max(ScenarioEvent.id))) or 0
    row.active = bool(channels)
    row.channels = sorted(set(channels))
    row.kinds = sorted(set(kinds))
    await session.commit()
    return {"active": row.active, "channels": row.channels, "kinds": row.kinds}


async def queue_deliveries(session):
    subscriptions = (await session.scalars(select(ScenarioSubscription).where(ScenarioSubscription.active.is_(True)).with_for_update(skip_locked=True))).all()
    for subscription in subscriptions:
        events = (await session.scalars(select(ScenarioEvent).where(
            ScenarioEvent.snapshot_id == subscription.snapshot_id,
            ScenarioEvent.id > subscription.after_event_id,
            ScenarioEvent.recorded_at >= subscription.created_at,
            ScenarioEvent.occurred_at >= subscription.created_at,
            ScenarioEvent.kind.in_(subscription.kinds),
        ))).all()
        for event in events:
            for channel in subscription.channels:
                exists = await session.scalar(select(ScenarioDelivery.id).where(ScenarioDelivery.subscription_id == subscription.id, ScenarioDelivery.event_id == event.id, ScenarioDelivery.channel == channel))
                if exists is None:
                    session.add(ScenarioDelivery(subscription_id=subscription.id, event_id=event.id, channel=channel))
    await session.commit()


async def deliver_pending(session, sender=None, now=None):
    now = now or datetime.now(timezone.utc)
    default_sender = sender is None
    if sender is None:
        from app.bot.push import send_message
        sender = send_message
    query = select(ScenarioDelivery, ScenarioSubscription, ScenarioEvent, ScenarioSnapshot).join(ScenarioSubscription, ScenarioSubscription.id == ScenarioDelivery.subscription_id).join(ScenarioEvent, ScenarioEvent.id == ScenarioDelivery.event_id).join(ScenarioSnapshot, ScenarioSnapshot.id == ScenarioEvent.snapshot_id).where(
        ScenarioDelivery.status == "pending", ScenarioDelivery.next_attempt_at <= now,
    ).order_by(ScenarioDelivery.id).limit(100).with_for_update(of=ScenarioDelivery, skip_locked=True)
    count = 0
    for delivery, subscription, event, snapshot in (await session.execute(query)).all():
        if not subscription.active or delivery.channel not in subscription.channels or event.id <= subscription.after_event_id or event.kind not in subscription.kinds:
            delivery.status = "cancelled"
            continue
        delivery.attempts += 1
        try:
            if delivery.channel == "telegram":
                if default_sender:
                    from app.config import get_settings
                    if not get_settings().telegram_bot_token:
                        raise ValueError("Telegram-бот не настроен")
                user = await session.get(User, subscription.user_id)
                if user is None or not user.telegram_chat_id:
                    raise ValueError("Telegram не привязан")
                await sender(user.telegram_chat_id, scenario_message(snapshot, event))
            delivery.status = "sent"
            delivery.sent_at = now
            delivery.error = ""
            count += 1
        except Exception as exc:
            delivery.error = type(exc).__name__
            delivery.status = "failed" if delivery.attempts >= 5 else "pending"
            delivery.next_attempt_at = now + timedelta(minutes=min(60, 2 ** delivery.attempts))
    await session.commit()
    return count


async def web_notifications(session, user_id):
    rows = (await session.execute(select(ScenarioSnapshot, ScenarioEvent).select_from(ScenarioEvent).join(ScenarioSnapshot, ScenarioSnapshot.id == ScenarioEvent.snapshot_id).join(ScenarioDelivery, ScenarioDelivery.event_id == ScenarioEvent.id).join(ScenarioSubscription, ScenarioSubscription.id == ScenarioDelivery.subscription_id).where(
        ScenarioSubscription.user_id == user_id, ScenarioDelivery.channel == "web", ScenarioDelivery.status == "sent", ScenarioSnapshot.id == ScenarioEvent.snapshot_id,
    ).order_by(ScenarioDelivery.id.desc()).limit(30))).all()
    return [{"snapshot_id": snapshot.id, "ticker": snapshot.ticker, "kind": event.kind, "occurred_at": event.occurred_at} for snapshot, event in rows]
