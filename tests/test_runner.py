import pytest

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
    argv = build_argv("backtest_asof", 7)
    assert "--horizon" in argv
    assert argv[argv.index("--horizon") + 1] == "7"


def test_build_argv_int_default():
    argv = build_argv("backtest_asof", None)
    assert argv[argv.index("--horizon") + 1] == "5"


def test_build_argv_text_param():
    argv = build_argv("update_oi", "SiU6")
    assert "--ticker" in argv
    assert argv[argv.index("--ticker") + 1] == "SiU6"


def test_build_argv_text_default():
    argv = build_argv("update_oi", None)
    assert argv[argv.index("--ticker") + 1] == "W4V6"


def test_build_argv_args_flag():
    argv = build_argv("update_oi_all", None)
    assert "--all" in argv
    assert "--ticker" not in argv


def test_build_argv_unknown_script():
    with pytest.raises(ValueError):
        build_argv("no_such_script", None)
