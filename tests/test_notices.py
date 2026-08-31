from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, ScriptRun, SystemNotice
from app.notices.service import (
    dismiss_all_notices,
    dismiss_notice,
    extract_script_title,
    resolve_script_run_notices,
)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as store:
        yield store
    await engine.dispose()


def _notice(text: str) -> SystemNotice:
    return SystemNotice(level="critical", text=text, source="script_run")


def _success_run(script_name: str, finished_at: datetime) -> ScriptRun:
    return ScriptRun(
        script_name=script_name,
        status="success",
        exit_code=0,
        output="ok",
        started_at=finished_at - timedelta(minutes=1),
        finished_at=finished_at,
    )


def test_extract_script_title():
    assert (
        extract_script_title("Скрипт «Ежедневный конвейер» завершился с ошибкой (код 1)")
        == "Ежедневный конвейер"
    )
    assert extract_script_title("что-то другое") == ""


async def test_resolve_removes_notice_when_newer_success(session):
    now = datetime.now(timezone.utc)
    notice = _notice(
        "Скрипт «Ежедневный конвейер» завершился с ошибкой (код 1)"
    )
    session.add(notice)
    session.add(_success_run("daily_pipeline", now + timedelta(minutes=5)))
    await session.commit()
    notice.created_at = now
    await session.commit()

    resolved = await resolve_script_run_notices(session)
    assert resolved == 1
    await session.refresh(notice)
    assert notice.is_active is False


async def test_resolve_keeps_notice_without_newer_success(session):
    now = datetime.now(timezone.utc)
    notice = _notice(
        "Скрипт «Ежедневный конвейер» завершился с ошибкой (код 1)"
    )
    session.add(notice)
    session.add(_success_run("daily_pipeline", now - timedelta(days=1)))
    await session.commit()
    notice.created_at = now
    await session.commit()

    resolved = await resolve_script_run_notices(session)
    assert resolved == 0
    await session.refresh(notice)
    assert notice.is_active is True


async def test_resolve_keeps_notice_for_other_script(session):
    now = datetime.now(timezone.utc)
    notice = _notice(
        "Скрипт «Ежедневный конвейер» завершился с ошибкой (код 1)"
    )
    session.add(notice)
    session.add(_success_run("collect_news", now + timedelta(minutes=5)))
    await session.commit()
    notice.created_at = now
    await session.commit()

    resolved = await resolve_script_run_notices(session)
    assert resolved == 0
    await session.refresh(notice)
    assert notice.is_active is True


async def test_dismiss_notice_marks_only_selected_notice_inactive(session):
    first = _notice("Первая ошибка")
    second = _notice("Вторая ошибка")
    session.add_all([first, second])
    await session.commit()

    assert await dismiss_notice(session, first.id) is True
    await session.refresh(first)
    await session.refresh(second)
    assert first.is_active is False
    assert second.is_active is True
    assert await dismiss_notice(session, first.id) is False


async def test_dismiss_all_notices_marks_all_active_notices_inactive(session):
    active = _notice("Активное")
    inactive = _notice("Снятое")
    inactive.is_active = False
    session.add_all([active, inactive])
    await session.commit()

    assert await dismiss_all_notices(session) == 1
    await session.refresh(active)
    await session.refresh(inactive)
    assert active.is_active is False
    assert inactive.is_active is False
