import asyncio
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings

settings = get_settings()
from app.db.connection import SessionLocal
from app.db.models import ScriptRun

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_FLUSH_INTERVAL = 1.5
LOG_FLUSH_LINES = 25

SCRIPTS: list[dict] = [
    {
        "key": "daily_pipeline",
        "module": "scripts.daily_pipeline",
        "title": "Ежедневный конвейер",
        "description": (
            "Полный ежедневный цикл: сбор и анализ новостей (RSS; "
            "сайты компаний — флаг --sites), "
            "синхронизация свечей MOEX, генерация стратегий по всем бумагам "
            "и генерация алертов с push в Telegram. Запускать ежедневно."
        ),
        "param": None,
        "params": [
            ("--from-phase", "Начать с фазы (1–5)", 1, "int"),
        ],
    },
    {
        "key": "collect_news",
        "module": "scripts.collect_news",
        "title": "Сбор новостей",
        "description": (
            "Сбор новостей из RSS-лент (сайты компаний — флаг --sites), "
            "фильтрация по сущностям графа "
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
        "key": "seed_watch_metrics",
        "module": "scripts.seed_watch_metrics",
        "title": "Наполнить метрики «что отслеживать»",
        "description": (
            "Кураторское наполнение подсказок «что отслеживать» для ключевых "
            "сущностей карты зависимостей (Нефть→Brent/WTI, Авиаперевозки→"
            "топливо/загрузка и т.п.). Идемпотентно — повторный запуск не "
            "дублирует метрики. Запускать после seed_db."
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
        "key": "calibrate_weights",
        "module": "scripts.calibrate_weights",
        "title": "Калибровка весов факторов",
        "description": (
            "Пересчёт весов факторов (news/graph) по накопленной обратной "
            "связи «сработало/не сработало»: создаёт новую версию весов "
            "(w1, w2, …), которую начинают использовать новые стратегии. "
            "Старые вердикты не пересчитываются (историчность). "
            "Запускать по мере накопления оценок — ориентировочно "
            "раз в 1–2 недели."
        ),
        "param": None,
    },
    {
        "key": "add_research",
        "module": "scripts.add_research",
        "title": "Добавить в граф: ссылка / PDF / граф влияния",
        "description": (
            "Дополнение knowledge graph связями и обоснованиями (FR-05-08): "
            "прикрепляет ссылку к существующему ребру или создаёт новое "
            "(и недостающие сущности). Одиночно --from/--to/--url, пакетно "
            "--file articles.csv, из PDF --pdf, либо из ASCII/текстового "
            "графа влияния --graph <файл> (LLM парсит схему в несколько связей). "
            "Повторные запуски не дублируют ссылки."
        ),
        "params": [
            ("--file", "CSV-файл со ссылками (from,to,url,...)", "", "text"),
            ("--graph", "Файл с ASCII/текстовым графом влияния", "", "text"),
        ],
    },
    {
        "key": "export_graph",
        "module": "scripts.export_graph",
        "title": "Экспорт графа (JSONL)",
        "description": (
            "Выгрузка всего графа — сущностей (type/aliases/meta) и рёбер "
            "(имена, direction/strength/kind/confidence/rationale/source_ref/"
            "created_by/is_approved) — в JSONL для переноса на новую БД или "
            "резервной копии. Восстановление: seed_db, затем import_graph."
        ),
        "params": [
            ("--out", "Файл дампа (JSONL); без него — в stdout", "", "text"),
        ],
        "hidden": True,
    },
    {
        "key": "import_graph",
        "module": "scripts.import_graph",
        "title": "Импорт графа (JSONL)",
        "description": (
            "Идемпотентный импорт графа из JSONL (формат export_graph): "
            "создаёт недостающие сущности и рёбра, дополняет source_ref без "
            "дублей. Использовать после пересоздания/переезда БД для "
            "восстановления связей и обоснований (в т.ч. FAQ/SEC)."
        ),
        "params": [
            ("--file", "JSONL-файл экспорта", "", "text"),
        ],
        "hidden": True,
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
    {
        "key": "update_oi",
        "module": "scripts.update_oi",
        "title": "Обновить открытые позиции (OI)",
        "timeout_seconds": 7200,
        "description": (
            "Скачивание истории открытых позиций фьючерсов из MOEX ISS "
            "(iss/history, блок history: OPENPOSITION, OPENPOSITIONVALUE) "
            "в таблицу market_open_positions и создание дневных свечей "
            "фьючерса (для сигналов «цена × OI»). Тикер — код фьючерса "
            "(например W4V6); поле «дней» работает и с тикером, и с флагом "
            "--all (по умолчанию 30; для полной истории используйте --from "
            "в CLI). Повторные запуски идемпотентны."
        ),
        "params": [
            ("--ticker", "Код фьючерса (SECID)", "W4V6", "text"),
            ("--days", "За последние N дней", 30, "int"),
            ("--tickers", "Шаблон фьючерсов (список SECID)", "", "templates"),
        ],
    },
    {
        "key": "update_oi_all",
        "module": "scripts.update_oi",
        "title": "Скачать OI по всем фьючерсам",
        "timeout_seconds": 7200,
        "description": (
            "Скачивание открытых позиций и свечей по ВСЕМ фьючерсам, "
            "доступным на MOEX (список берётся из ISS, ~467 контрактов, "
            "полная доступная история). Запускается чекбоксом на карточке "
            "«Обновить открытые позиции (OI)»."
        ),
        "param": None,
        "args": ["--all"],
        "hidden": True,
    },
    {
        "key": "realtime_updater",
        "module": "scripts.realtime_updater",
        "title": "Актуализация рынка (демон)",
        "no_timeout": True,
        "description": (
            "Демон-процесс актуализации рыночных данных в реальном времени: "
            "live-котировки, дневные свечи и OI фьючерсов по настройкам "
            "(тумблер включения, интервалы, шаблон фьючерсов) в админ-панели "
            "«Реальное время». Запускать как демон, когда нужна актуальность "
            "данных; останавливать при необходимости. Не собирает новости. "
            "Настройки в админ-панели, блок «Реальное время»."
        ),
        "param": None,
    },
]

SCRIPTS_BY_KEY = {s["key"]: s for s in SCRIPTS}

_active_run_ids: dict[str, int | None] = {"regular": None, "daemon": None}


def _run_slot(script_key: str) -> str:
    script = get_script(script_key)
    return "daemon" if script is not None and script.get("no_timeout") else "regular"


def get_script(key: str) -> dict | None:
    return SCRIPTS_BY_KEY.get(key)


def script_timeout_seconds(script_key: str) -> int | None:
    """Таймаут запуска скрипта (сек).

    Приоритет: env `SCRIPT_TIMEOUT_SECONDS_<KEY>` (например
    SCRIPT_TIMEOUT_SECONDS_UPDATE_OI) > дефолт скрипта в SCRIPTS
    (timeout_seconds) > глобальный settings.script_timeout_seconds.
    Для демонов (realtime_updater) при `no_timeout: True` возвращается None —
    asyncio.timeout(None) отменяет таймаут (демон живёт до остановки).
    """
    if get_script(script_key) is not None and get_script(script_key).get("no_timeout"):
        env_value = os.getenv(f"SCRIPT_TIMEOUT_SECONDS_{script_key.upper()}")
        if env_value:
            try:
                return int(env_value)
            except ValueError:
                pass
        return None
    env_value = os.getenv(f"SCRIPT_TIMEOUT_SECONDS_{script_key.upper()}")
    if env_value:
        try:
            return int(env_value)
        except ValueError:
            pass
    script = get_script(script_key)
    if script and script.get("timeout_seconds"):
        return int(script["timeout_seconds"])
    return settings.script_timeout_seconds


def build_argv(script_key: str, param_values: dict | None = None) -> list[str]:
    script = get_script(script_key)
    if script is None:
        raise ValueError(f"Неизвестный скрипт: {script_key}")
    param_values = param_values or {}
    argv = [sys.executable, "-u", "-m", script["module"]]
    argv += script.get("args", [])

    params = script.get("params")
    if params:
        handled = set()
        for p in params:
            flag, label, default, *rest = p
            ptype = rest[0] if rest else "int"
            value = param_values.get(flag, default)
            handled.add(flag)
            if ptype in ("text", "templates"):
                argv += [flag, str(value) if value not in (None, "") else ""]
            else:
                value = int(value) if value is not None else int(default)
                if value <= 0:
                    raise ValueError("Параметр должен быть положительным числом")
                argv += [flag, str(value)]
        # Дополнительные параметры из param_values, не объявленные в SCRIPTS
        # (например --tickers для daily_pipeline, подставляемые сервером)
        for flag, value in (param_values or {}).items():
            if flag in handled or value in (None, ""):
                continue
            argv += [flag, str(value)]
        return argv

    param = script["param"]
    if param is not None:
        flag = param[0]
        ptype = param[3] if len(param) > 3 else "int"
        value = param_values.get(flag)
        if ptype == "text":
            value = str(value) if value not in (None, "") else str(param[2])
            argv += [flag, value]
        else:
            value = int(value) if value is not None else int(param[2])
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


async def _append_output(run_id: int, text: str) -> None:
    async with SessionLocal() as session:
        run = await session.get(ScriptRun, run_id)
        if run is None:
            return
        run.output = (run.output or "") + text
        await session.commit()


async def _execute(run_id: int, script_key: str, param_values: dict | None) -> tuple[int, str]:
    argv = build_argv(script_key, param_values)
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(PROJECT_ROOT),
        env=env,
    )
    all_parts: list[str] = []
    pending: list[str] = []
    last_flush = time.monotonic()
    exit_code = -1

    def _flush_due() -> bool:
        return (len(pending) >= LOG_FLUSH_LINES) or (
            bool(pending) and time.monotonic() - last_flush >= LOG_FLUSH_INTERVAL
        )

    try:
        async with asyncio.timeout(script_timeout_seconds(script_key)):
            assert proc.stdout is not None
            while True:
                chunk = await proc.stdout.readline()
                if not chunk:
                    break
                decoded = chunk.decode("utf-8", errors="replace")
                all_parts.append(decoded)
                pending.append(decoded)
                if _flush_due():
                    await _append_output(run_id, "".join(pending))
                    pending = []
                    last_flush = time.monotonic()
            # Ждём полного завершения процесса: returncode может быть ещё None,
            # когда stdout уже закрылся (процесс упал) — иначе exit_code = 0 («ложный успех»)
            await proc.wait()
            exit_code = proc.returncode or 0
    except (asyncio.TimeoutError, TimeoutError):
        proc.kill()
        await proc.wait()
        exit_code = 124
        note = "\nПревышено время ожидания (30 минут), процесс остановлен\n"
        all_parts.append(note)
        pending.append(note)
    finally:
        if pending:
            await _append_output(run_id, "".join(pending))
    return exit_code, "".join(all_parts)


_PIPELINE_PHASE_RE = re.compile(r"Фаза (\d)/5: (.+?)\.\.\.")


def _pipeline_failed_phase(output: str | None) -> str | None:
    """Фаза Ежедневного конвейера, на которой произошёл сбой (из лога)."""
    if not output:
        return None
    matches = list(_PIPELINE_PHASE_RE.finditer(output))
    if not matches:
        return None
    last = matches[-1]
    return f"{last.group(1)}/5: {last.group(2).strip()}"


async def run_script_task(run_id: int, script_key: str, param_values: dict | None) -> None:
    status = "failed"
    exit_code = -1
    output = "Скрипт не выполнен: ошибка запуска"
    try:
        await _mark_status(run_id, status="running")
        try:
            exit_code, output = await _execute(run_id, script_key, param_values)
            # Страховка: падение процесса с закрытым stdout иногда даёт код 0
            if exit_code == 0 and "Traceback (most recent call last)" in output:
                exit_code = 1
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
        if status == "failed":
            script = get_script(script_key)
            title = script["title"] if script else script_key
            phase = _pipeline_failed_phase(output) if script_key == "daily_pipeline" else None
            try:
                from app.notices.service import notify_script_failed

                await notify_script_failed(title, exit_code, phase=phase)
            except Exception:
                pass
        elif status == "success":
            try:
                from app.db.connection import SessionLocal
                from app.notices.service import resolve_script_run_notices

                async with SessionLocal() as session:
                    await resolve_script_run_notices(session)
            except Exception:
                pass
        slot = _run_slot(script_key)
        if _active_run_ids.get(slot) == run_id:
            _active_run_ids[slot] = None


def launch(run_id: int, script_key: str, param_values: dict | None) -> None:
    slot = _run_slot(script_key)
    if _active_run_ids[slot] is not None:
        if slot == "daemon":
            raise RuntimeError("Демон уже выполняется")
        raise RuntimeError("Другой скрипт уже выполняется")
    _active_run_ids[slot] = run_id
    asyncio.get_running_loop().create_task(run_script_task(run_id, script_key, param_values))


def is_busy() -> bool:
    return _active_run_ids["regular"] is not None


def is_daemon_busy() -> bool:
    return _active_run_ids["daemon"] is not None


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
        from app.notices.service import add_notice

        await add_notice(
            session,
            "critical",
            f"Скрипт «{run.script_name}» был прерван перезапуском сервиса",
            source="script_run",
        )
    if stale:
        await session.commit()
    return len(stale)


async def recover_stale_runs() -> int:
    async with SessionLocal() as session:
        return await mark_stale_runs(session)
