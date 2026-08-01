from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.service import get_settings
from app.bot.push import send_message
from app.config import get_settings as get_app_settings
from app.db.models import Alert, Security, User
from app.presentation.factories import build_alert_message


async def deliver_telegram(session: AsyncSession, alerts: list[Alert]) -> int:
    if not alerts:
        return 0
    app_settings = get_app_settings()
    tickers: dict[int, str] = {}
    users: dict[int, User | None] = {}
    channels: dict[int, set[str]] = {}
    sent = 0
    for alert in alerts:
        if alert.user_id not in users:
            users[alert.user_id] = await session.get(User, alert.user_id)
        user = users[alert.user_id]
        if user is None or not user.telegram_chat_id:
            continue
        if alert.user_id not in channels:
            settings = await get_settings(session, alert.user_id)
            channels[alert.user_id] = set(settings.channels or [])
        if "telegram" not in channels[alert.user_id]:
            continue
        if alert.security_id not in tickers:
            security = await session.get(Security, alert.security_id)
            tickers[alert.security_id] = security.ticker if security else ""
        text = build_alert_message(
            ticker=tickers[alert.security_id],
            headline=alert.headline,
            url=alert.url,
            impact=alert.impact,
            is_ambiguous=alert.is_ambiguous,
            web_url=f"{app_settings.app_url}/securities/{tickers[alert.security_id]}",
        )
        try:
            await send_message(user.telegram_chat_id, text)
            sent += 1
        except Exception:
            continue
    return sent
