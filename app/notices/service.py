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
    state = "critical" if critical else ("warning" if notices else "none")
    return {
        "state": state,
        "notices": [
            {
                "level": notice.level,
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
        await set_source_notice(
            session, "telegram_health", "warning", text, active=True
        )
