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


async def test_stop_terminates_live_process_and_records_initiator():
    class Process:
        returncode = None

        def terminate(self):
            self.returncode = -15

        async def wait(self):
            return self.returncode

    process = Process()
    runner._active_processes[501] = process
    try:
        assert await runner.stop(501, 17) is True
        assert process.returncode == -15
        assert runner._stop_requests[501] == 17
    finally:
        runner._active_processes.pop(501, None)
        runner._stop_requests.pop(501, None)


async def test_stop_rejects_completed_or_unknown_process():
    assert await runner.stop(999, 17) is False


def test_top5_modes_bounds_and_timeout():
    once = build_argv("evaluate_top5")
    assert "--loop" not in once
    assert once[once.index("--interval") + 1] == "300"
    continuous = build_argv("evaluate_top5", {"--loop": True, "--interval": 60, "--limit": 25})
    assert continuous[-1] == "--loop"
    assert continuous[continuous.index("--interval") + 1] == "60"
    assert runner.script_timeout_seconds("evaluate_top5", {"--loop": True}) is None
    assert runner.script_timeout_seconds("evaluate_top5", {"--loop": False}) is not None
    for params in ({"--interval": 29}, {"--limit": 1001}, {"--loop": "false"}):
        with pytest.raises(ValueError):
            build_argv("evaluate_top5", params)


def test_top5_monitor_has_independent_execution_slot(monkeypatch):
    def create_task(coro):
        coro.close()
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: type("Loop", (), {"create_task": staticmethod(create_task)})())
    monkeypatch.setattr(runner, "_active_run_ids", {"regular": None, "daemon": None, "top5": None})
    runner.launch(1, "evaluate_top5", {"--loop": True})
    runner.launch(2, "realtime_updater", None)
    runner.launch(3, "update_oi", None)
    assert runner.active_run_id("evaluate_top5") == 1
    assert runner.is_busy() and runner.is_daemon_busy()
    with pytest.raises(RuntimeError):
        runner.launch(4, "evaluate_top5", None)


async def test_idle_script_output_is_flushed_before_exit(monkeypatch):
    stream = asyncio.StreamReader()
    flushed = asyncio.Event()
    output = []
    class Process:
        stdout = stream
        returncode = 0
        async def wait(self):
            return 0
    async def spawn(*args, **kwargs):
        return Process()
    async def append(run_id, text):
        output.append(text)
        flushed.set()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(runner, "_append_output", append)
    monkeypatch.setattr(runner, "LOG_FLUSH_INTERVAL", 0.01)
    stream.feed_data("Режим: непрерывно\n".encode())
    task = asyncio.create_task(runner._execute(88, "evaluate_top5", {"--loop": True}))
    try:
        await asyncio.wait_for(flushed.wait(), 1)
        assert not task.done()
        assert "непрерывно" in "".join(output)
    finally:
        stream.feed_eof()
        result = await task
    assert result[0] == 0


@pytest.mark.parametrize("loop_value,interval,limit,accepted", [
    (None, "60", "25", True), ("1", "60", "25", True),
    ("1", "29", "25", False), ("1", "60", "1001", False), ("1", "bad", "25", False),
])
async def test_top5_admin_form_launches_selected_mode(monkeypatch, loop_value, interval, limit, accepted):
    from types import SimpleNamespace
    from urllib.parse import urlencode
    from starlette.requests import Request
    from app.web import router as web
    recorded = []
    class Store:
        def add(self, row):
            row.id = 77
        async def commit(self):
            pass
    async def admin(*args):
        return SimpleNamespace(id=1, username="admin", role="admin")
    monkeypatch.setattr(web, "_optional_user", admin)
    monkeypatch.setattr(web, "launch", lambda *args: recorded.append(args))
    monkeypatch.setattr(web, "active_run_id", lambda key: None)
    form = {"script": "evaluate_top5", "param_limit": limit, "param_interval": interval}
    if loop_value:
        form["param_loop"] = loop_value
    async def receive():
        return {"type": "http.request", "body": urlencode(form).encode()}
    request = Request({"type": "http", "method": "POST", "path": "/admin/scripts/run", "headers": [(b"content-type", b"application/x-www-form-urlencoded")]}, receive)
    response = await web.admin_run_script(request, Store())
    assert response.status_code == 303
    if accepted:
        assert recorded[0][2] == {"--limit": 25, "--interval": 60, "--loop": bool(loop_value)}
    else:
        assert not recorded
        assert response.headers["location"] == "/admin?error=2"
