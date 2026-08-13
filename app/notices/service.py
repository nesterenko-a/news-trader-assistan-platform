import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ScriptRun, SystemNotice


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


async def notify_script_failed(title: str, exit_code: int, phase: str | None = None) -> None:
    from app.db.connection import SessionLocal

    text = f"Скрипт «{title}» завершился с ошибкой (код {exit_code})"
    if phase:
        text += f" на фазе {phase}"
    async with SessionLocal() as session:
        await add_notice(session, "critical", text, source="script_run")


_TITLE_RE = re.compile(r"Скрипт «(.+?)» завершился")


def extract_script_title(text: str) -> str:
    """Название скрипта из текста уведомления «Скрипт «X» завершился…»."""
    match = _TITLE_RE.search(text or "")
    return match.group(1) if match else ""


async def resolve_script_run_notices(session: AsyncSession) -> int:
    """Снимает активные уведомления о сбое скрипта (source=script_run),
    если в истории «Последние запуски» (ScriptRun) есть более поздний
    успешный запуск того же скрипта (finished_at > created_at уведомления).

    Возвращает количество снятых уведомлений.
    """
    from app.admin.runner import SCRIPTS

    notices = (
        await session.scalars(
            select(SystemNotice).where(
                SystemNotice.source == "script_run",
                SystemNotice.is_active.is_(True),
            )
        )
    ).all()
    if not notices:
        return 0

    title_to_keys: dict[str, list[str]] = {}
    for script in SCRIPTS:
        title_to_keys.setdefault(script.get("title", ""), []).append(
            script.get("key", "")
        )

    resolved = 0
    for notice in notices:
        title = extract_script_title(notice.text)
        keys = title_to_keys.get(title) or []
        if not keys or notice.created_at is None:
            continue
        has_newer_success = await session.scalar(
            select(func.count())
            .select_from(ScriptRun)
            .where(
                ScriptRun.script_name.in_(keys),
                ScriptRun.status == "success",
                ScriptRun.finished_at > notice.created_at,
            )
        )
        if has_newer_success:
            notice.is_active = False
            resolved += 1
    if resolved:
        await session.commit()
    return resolved


async def notify_telegram_unavailable(error: str) -> None:
    from app.db.connection import SessionLocal
    from app.notices.monitor import set_source_notice

    text = f"Нет подключения к Telegram-боту: {error[:300]}"
    async with SessionLocal() as session:
        await set_source_notice(session, "telegram", "warning", text, active=True)
