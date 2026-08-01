import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.db.connection import SessionLocal
from app.db.models import ScriptRun

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_TIMEOUT_SECONDS = 1800

SCRIPTS: list[dict] = [
    {
        "key": "daily_pipeline",
        "module": "scripts.daily_pipeline",
        "title": "Ежедневный конвейер",
        "description": (
            "Полный ежедневный цикл: сбор и анализ новостей (RSS), "
            "синхронизация свечей MOEX, генерация стратегий по всем бумагам "
            "и генерация алертов с push в Telegram. Запускать ежедневно."
        ),
        "param": None,
    },
    {
        "key": "collect_news",
        "module": "scripts.collect_news",
        "title": "Сбор новостей",
        "description": (
            "Сбор новостей из RSS-лент, фильтрация по сущностям графа "
            "и LLM-анализ (тематика, тональность, значимость). "
            "Параметр --days — за сколько последних дней брать новости "
            "(для бэкфилла истории укажите больше, например 30)."
        ),
        "param": ("--days", "Окно сбора (дней)", 7),
    },
    {
        "key": "update_prices",
        "module": "scripts.update_prices",
        "title": "Обновить цены",
        "description": (
            "Синхронизация дневных свечей из MOEX ISS по всем бумагам. "
            "Не требует LLM-ключа. Параметр --days — за сколько последних "
            "календарных дней (с запасом на выходные и праздники)."
        ),
        "param": ("--days", "За последние N дней", 5),
    },
    {
        "key": "process_alerts",
        "module": "scripts.process_alerts",
        "title": "Сгенерировать алерты",
        "description": (
            "Генерация алертов по значимым новостям для watchlist всех "
            "пользователей и push в Telegram тем, кто включил канал. "
            "Параметр --days — окно новостей."
        ),
        "param": ("--days", "Окно новостей (дней)", 7),
    },
    {
        "key": "seed_macro",
        "module": "scripts.seed_macro",
        "title": "Наполнить макрокалендарь",
        "description": (
            "Наполнение макрокалендаря на 6 месяцев вперёд: заседания ЦБ, "
            "CPI/PMI/ВВП, сезоны отчётностей и корпоративные события. "
            "Идемпотентно — повторный запуск не дублирует события."
        ),
        "param": None,
    },
    {
        "key": "seed_db",
        "module": "scripts.seed_db",
        "title": "Наполнить справочники",
        "description": (
            "Наполнение справочников: бумаги, сущности и связи knowledge graph. "
            "Запускать один раз при старте проекта (идемпотентно)."
        ),
        "param": None,
    },
    {
        "key": "calibrate",
        "module": "scripts.calibrate",
        "title": "Калибровка порогов",
        "description": (
            "Анализ распределения net-score по всем бумагам без сохранения "
            "стратегий — для настройки порогов вердиктов. "
            "Рекомендуется раз в неделю после накопления новостей."
        ),
        "param": None,
    },
    {
        "key": "backtest",
        "module": "scripts.backtest",
        "title": "Бэктест",
        "description": (
            "Оценка сохранённых вердиктов против фактического движения цены: "
            "вход = close на дату генерации, выход = close через 5 торговых дней. "
            "Полезен раз в 1–2 недели, когда накопились вердикты."
        ),
        "param": None,
    },
    {
        "key": "backtest_asof",
        "module": "scripts.backtest_asof",
        "title": "Бэктест «на момент T»",
        "description": (
            "Реконструкция вердиктов по историческим данным: на каждую дату T "
            "используются только данные, доступные в T (без заглядывания "
            "в будущее), результат оценивается через N торговых дней. "
            "Нужны новости и свечи за период. Параметр --horizon — горизонт."
        ),
        "param": ("--horizon", "Горизонт (торговых дней)", 5),
    },
]

SCRIPTS_BY_KEY = {s["key"]: s for s in SCRIPTS}

_active_run_id: int | None = None


def get_script(key: str) -> dict | None:
    return SCRIPTS_BY_KEY.get(key)


def build_argv(script_key: str, param_value: int | None) -> list[str]:
    script = get_script(script_key)
    if script is None:
        raise ValueError(f"Неизвестный скрипт: {script_key}")
    argv = [sys.executable, "-m", script["module"]]
    param = script["param"]
    if param is not None:
        flag = param[0]
        value = int(param_value) if param_value is not None else int(param[2])
        if value <= 0:
            raise ValueError("Параметр должен быть положительным числом")
        argv += [flag, str(value)]
    return argv


async def _mark_status(run_id: int, **fields) -> None:
    async with SessionLocal() as session:
        run = await session.get(ScriptRun, run_id)
        if run is None:
            return
        for key, value in fields.items():
            setattr(run, key, value)
        await session.commit()


async def _execute(script_key: str, param_value: int | None) -> tuple[int, str]:
    argv = build_argv(script_key, param_value)
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(PROJECT_ROOT),
    )
    try:
        raw = await asyncio.wait_for(proc.communicate(), timeout=SCRIPT_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return 124, "Превышено время ожидания (30 минут), процесс остановлен"
    output = (raw[0] or b"").decode("utf-8", errors="replace")
    return proc.returncode or 0, output


async def run_script_task(run_id: int, script_key: str, param_value: int | None) -> None:
    status = "failed"
    exit_code = -1
    output = "Скрипт не выполнен: ошибка запуска"
    try:
        await _mark_status(run_id, status="running")
        try:
            exit_code, output = await _execute(script_key, param_value)
            status = "success" if exit_code == 0 else "failed"
        except Exception as exc:
            exit_code = -1
            status = "failed"
            output = f"{type(exc).__name__}: {exc}\n"
    finally:
        try:
            await _mark_status(
                run_id,
                status=status,
                exit_code=exit_code,
                output=output,
                finished_at=datetime.now(timezone.utc),
            )
        except Exception:
            pass
        global _active_run_id
        _active_run_id = None


def launch(run_id: int, script_key: str, param_value: int | None) -> None:
    global _active_run_id
    if _active_run_id is not None:
        raise RuntimeError("Другой скрипт уже выполняется")
    _active_run_id = run_id
    asyncio.get_running_loop().create_task(run_script_task(run_id, script_key, param_value))


def is_busy() -> bool:
    return _active_run_id is not None


async def mark_stale_runs(session) -> int:
    stale = (
        await session.scalars(select(ScriptRun).where(ScriptRun.status == "running"))
    ).all()
    now = datetime.now(timezone.utc)
    for run in stale:
        run.status = "failed"
        run.exit_code = -1
        run.output = (
            f"{run.output or ''}\n[прервано: сервис перезапущен до завершения]"
        ).strip()
        run.finished_at = now
    if stale:
        await session.commit()
    return len(stale)


async def recover_stale_runs() -> int:
    async with SessionLocal() as session:
        return await mark_stale_runs(session)
