from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, ScriptRun, SystemNotice
from app.notices.service import (
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
