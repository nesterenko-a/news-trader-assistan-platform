import asyncio

import pytest

from app.admin import runner
from app.admin.runner import SCRIPTS, build_argv
from app.web.router import _run_progress


def test_update_oi_all_hidden_entry():
    entry = next(s for s in SCRIPTS if s["key"] == "update_oi_all")
    assert entry.get("hidden") is True
    assert entry.get("args") == ["--all"]


def test_run_progress_parsing():
    out = "Найдено фьючерсов: 467\n  [1/467] A: +10\n  [2/467] B: +5\n"
    assert _run_progress(out) == {"done": 2, "total": 467, "remaining": 465, "pct": 0}


def test_run_progress_empty_or_absent():
    assert _run_progress(None) is None
    assert _run_progress("нет прогресса в логе") is None


def test_build_argv_int_param():
    argv = build_argv("backtest_asof", {"--horizon": 7})
    assert "--horizon" in argv
    assert argv[argv.index("--horizon") + 1] == "7"


def test_build_argv_int_default():
    argv = build_argv("backtest_asof", None)
    assert argv[argv.index("--horizon") + 1] == "5"


def test_build_argv_text_param():
    argv = build_argv("update_oi", {"--ticker": "SiU6"})
    assert argv[argv.index("--ticker") + 1] == "SiU6"
    assert argv[argv.index("--days") + 1] == "30"


def test_build_argv_text_default():
    argv = build_argv("update_oi", None)
    assert argv[argv.index("--ticker") + 1] == "W4V6"
    assert argv[argv.index("--days") + 1] == "30"


def test_build_argv_days_param():
    argv = build_argv("update_oi", {"--ticker": "W4V6", "--days": 60})
    assert argv[argv.index("--days") + 1] == "60"


def test_build_argv_args_flag():
    argv = build_argv("update_oi_all", None)
    assert "--all" in argv
    assert "--ticker" not in argv


def test_build_argv_unknown_script():
    with pytest.raises(ValueError):
        build_argv("no_such_script", None)


def test_build_argv_extra_param_passthrough():
    """Необъявленные в SCRIPTS параметры (например --tickers у daily_pipeline)
    пробрасываются в argv, если заданы в param_values."""
    argv = build_argv("daily_pipeline", {"--from-phase": 2, "--tickers": "W4V6,SRZ6"})
    assert "--tickers" in argv
    assert argv[argv.index("--tickers") + 1] == "W4V6,SRZ6"

    # Без --tickers в param_values он не добавляется
    argv = build_argv("daily_pipeline", {"--from-phase": 1})
    assert "--tickers" not in argv


def test_realtime_daemon_does_not_block_regular_script_launch(monkeypatch):
    started = []

    def fake_create_task(coro):
        started.append(coro)
        coro.close()
        return None

    monkeypatch.setattr(
        asyncio,
        "get_running_loop",
        lambda: type("Loop", (), {"create_task": staticmethod(fake_create_task)})(),
    )
    runner._active_run_ids.update({"regular": None, "daemon": None})
    try:
        runner.launch(101, "realtime_updater", None)
        runner.launch(102, "update_oi", None)
        assert runner.is_daemon_busy() is True
        assert runner.is_busy() is True
        with pytest.raises(RuntimeError, match="Демон уже выполняется"):
            runner.launch(103, "realtime_updater", None)
        with pytest.raises(RuntimeError, match="Другой скрипт"):
            runner.launch(104, "daily_pipeline", None)
        assert len(started) == 2
    finally:
        runner._active_run_ids.update({"regular": None, "daemon": None})
