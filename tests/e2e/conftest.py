"""Фикстуры и хелперы e2e-тестов веб-интерфейса (Playwright).

Автономный стенд: session-фикстура поднимает uvicorn-субпроцесс с отдельной
SQLite-БД (сидинг: граф, справочник бумаг, макро-события, admin/user,
стратегия SBER). Страницы открываются в chromium (фикстура `page`
pytest-playwright, sync API).

E2E-тесты исключены из общего прогона pytest (addopts -m "not e2e"): sync API
Playwright оставляет свой running event loop и конфликтует с pytest-asyncio.
Запуск: pytest -m e2e.
"""

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import hash_password
from app.db.connection import Base
from app.db.models import MacroEvent, Security, Strategy, User
from app.graph.service import seed_graph

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"
USER_USERNAME = "user"
USER_PASSWORD = "user123"

_READY_TIMEOUT = 60.0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _seed(db_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{db_path.as_posix()}"

    async def _run() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            await seed_graph(session)
            if (
                await session.scalar(select(User).where(User.username == ADMIN_USERNAME))
                is None
            ):
                session.add(
                    User(
                        username=ADMIN_USERNAME,
                        password_hash=hash_password(ADMIN_PASSWORD),
                        role="admin",
                    )
                )
            if (
                await session.scalar(select(User).where(User.username == USER_USERNAME))
                is None
            ):
                session.add(
                    User(
                        username=USER_USERNAME,
                        password_hash=hash_password(USER_PASSWORD),
                        role="user",
                    )
                )
            sber = await session.scalar(
                select(Security).where(Security.ticker == "SBER")
            )
            if sber is not None:
                existing = await session.scalar(
                    select(Strategy).where(Strategy.security_id == sber.id)
                )
                if existing is None:
                    session.add(
                        Strategy(
                            security_id=sber.id,
                            verdict="BUY",
                            horizon="medium",
                            confidence="medium",
                            model_version="mvp-0.1",
                            rationale_summary="e2e seed",
                        )
                    )
            # макро-события для страницы /macro (фильтры рендерятся только при наличии событий)
            macro_seed = [
                {
                    "event_type": "central_bank_meeting",
                    "title": "Заседание Банка России по ключевой ставке",
                    "region": "RU",
                    "expected_impact": "high",
                    "market_wide": True,
                },
                {
                    "event_type": "cpi",
                    "title": "Публикация индекса потребительских цен РФ",
                    "region": "RU",
                    "expected_impact": "medium",
                    "market_wide": True,
                },
                {
                    "event_type": "cpi",
                    "title": "Публикация CPI США",
                    "region": "US",
                    "expected_impact": "low",
                    "market_wide": True,
                },
            ]
            for item in macro_seed:
                if (
                    await session.scalar(
                        select(MacroEvent).where(MacroEvent.title == item["title"])
                    )
                    is None
                ):
                    session.add(
                        MacroEvent(
                            event_type=item["event_type"],
                            title=item["title"],
                            event_time=datetime.now(timezone.utc) + timedelta(days=3),
                            region=item["region"],
                            expected_impact=item["expected_impact"],
                            market_wide=item["market_wide"],
                        )
                    )
            await session.commit()
        await engine.dispose()

    # pytest-asyncio (auto) держит event loop в потоке теста — asyncio.run() и
    # run_until_complete() из него запрещены, поэтому сидинг выполняется в
    # отдельном потоке со своим loop.
    thread_result: list[BaseException | None] = [None]

    def _worker() -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_run())
        except BaseException as exc:  # noqa: BLE001 — пробрасываем в основной поток
            thread_result[0] = exc
        finally:
            loop.close()

    thread = threading.Thread(target=_worker)
    thread.start()
    thread.join()
    if thread_result[0] is not None:
        raise thread_result[0]


def _wait_ready(port: int) -> None:
    deadline = time.monotonic() + _READY_TIMEOUT
    last_error = "сервер не ответил"
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"http://127.0.0.1:{port}/v1/health", timeout=2.0)
            if resp.status_code == 200:
                return
            last_error = f"health status {resp.status_code}"
        except httpx.HTTPError as exc:
            last_error = str(exc)
        time.sleep(0.25)
    raise RuntimeError(
        f"тестовый сервер не поднялся за {_READY_TIMEOUT:.0f}s: {last_error}"
    )


@pytest.fixture(scope="session")
def server() -> str:
    """Поднимает uvicorn с тестовой SQLite-БД и возвращает базовый URL."""
    port = _free_port()
    with tempfile.TemporaryDirectory(prefix="newstrader_e2e_") as tmp:
        db_path = Path(tmp) / "e2e.db"
        _seed(db_path)
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path.as_posix()}"
        proc = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            env=env,
            cwd=Path(__file__).resolve().parents[2],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            _wait_ready(port)
            yield f"http://127.0.0.1:{port}"
        finally:
            # На Windows terminate() не убивает дочерние процессы (фоновые
            # скрипты админки), поэтому убиваем дерево через taskkill /T /F.
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                )
            else:
                proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
            # Windows: файл БД может быть кратковременно занят после
            # завершения процессов (в т.ч. фоновых скриптов админки) —
            # освобождаем с повторами до 30 секунд.
            for _ in range(60):
                try:
                    db_path.unlink()
                    break
                except OSError:
                    time.sleep(0.5)


def register(page, server_url: str, username: str, password: str) -> None:
    page.goto(server_url + "/register")
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]')
    page.wait_for_load_state("domcontentloaded")


def login(page, server_url: str, username: str, password: str) -> None:
    page.goto(server_url + "/login")
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.click('button[type="submit"]')
    page.wait_for_load_state("domcontentloaded")
