import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.db.connection import SessionLocal
from app.db.models import ScriptRun

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_TIMEOUT_SECONDS = 1800

SCRIPTS: list[dict] = [
    {
        "key": "daily_pipeline",
        "module": "scripts.daily_pipeline",
        "title": "Ежедневный конвейер",
        "description": "Новости → цены → стратегии → алерты",
        "param": None,
    },
    {
        "key": "collect_news",
        "module": "scripts.collect_news",
        "title": "Сбор новостей",
        "description": "RSS (+ Telegram при --telegram) и LLM-анализ",
        "param": ("--days", "Окно сбора, дней", 7),
    },
    {
        "key": "update_prices",
        "module": "scripts.update_prices",
        "title": "Обновить цены",
        "description": "Синхронизация свечей MOEX",
        "param": ("--days", "За последние N дней", 5),
    },
    {
        "key": "process_alerts",
        "module": "scripts.process_alerts",
        "title": "Сгенерировать алерты",
        "description": "По watchlist всех пользователей",
        "param": ("--days", "Окно новостей, дней", 7),
    },
    {
        "key": "seed_macro",
        "module": "scripts.seed_macro",
        "title": "Наполнить макрокалендарь",
        "description": "События на 6 месяцев вперёд",
        "param": None,
    },
    {
        "key": "seed_db",
        "module": "scripts.seed_db",
        "title": "Наполнить справочники",
        "description": "Бумаги, сущности, связи графа",
        "param": None,
    },
    {
        "key": "calibrate",
        "module": "scripts.calibrate",
        "title": "Калибровка порогов",
        "description": "Анализ распределения скоринга",
        "param": None,
    },
    {
        "key": "backtest",
        "module": "scripts.backtest",
        "title": "Бэктест",
        "description": "Оценка сохранённых вердиктов",
        "param": None,
    },
    {
        "key": "backtest_asof",
        "module": "scripts.backtest_asof",
        "title": "Бэктест «на момент T»",
        "description": "Реконструкция вердиктов по историческим данным",
        "param": ("--horizon", "Горизонт, торговых дней", 5),
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
    try:
        await _mark_status(run_id, status="running")
        try:
            exit_code, output = await _execute(script_key, param_value)
            status = "success" if exit_code == 0 else "failed"
        except Exception as exc:
            exit_code = -1
            status = "failed"
            output = f"{type(exc).__name__}: {exc}\n"
        await _mark_status(
            run_id,
            status=status,
            exit_code=exit_code,
            output=output,
            finished_at=datetime.now(timezone.utc),
        )
    finally:
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
