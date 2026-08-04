from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SystemNotice


async def add_notice(
    session: AsyncSession, level: str, text: str, source: str = ""
) -> SystemNotice:
    notice = SystemNotice(level=level, text=text, source=source)
    session.add(notice)
    await session.commit()
    return notice


async def notice_state(session: AsyncSession, limit: int = 50) -> dict:
    notices = (
        await session.scalars(
            select(SystemNotice)
            .where(SystemNotice.is_active.is_(True))
            .order_by(SystemNotice.id.desc())
            .limit(limit)
        )
    ).all()
    critical = sum(1 for n in notices if n.level == "critical")
    warning = sum(1 for n in notices if n.level == "warning")
    info = sum(1 for n in notices if n.level == "info")
    if critical:
        state = "critical"
    elif warning:
        state = "warning"
    elif info:
        state = "info"
    else:
        state = "none"
    return {
        "state": state,
        "notices": [
            {
                "level": notice.level,
                "source": notice.source,
                "text": notice.text,
                "created_at": notice.created_at.strftime("%d.%m.%Y %H:%M")
                if notice.created_at
                else "",
            }
            for notice in notices
        ],
    }


async def notify_script_failed(title: str, exit_code: int) -> None:
    from app.db.connection import SessionLocal

    text = f"Скрипт «{title}» завершился с ошибкой (код {exit_code})"
    async with SessionLocal() as session:
        await add_notice(session, "critical", text, source="script_run")


async def notify_telegram_unavailable(error: str) -> None:
    from app.db.connection import SessionLocal
    from app.notices.monitor import set_source_notice

    text = f"Нет подключения к Telegram-боту: {error[:300]}"
    async with SessionLocal() as session:
        await set_source_notice(session, "telegram", "warning", text, active=True)
