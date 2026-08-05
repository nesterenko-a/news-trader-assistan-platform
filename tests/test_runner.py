import pytest

from app.admin.runner import build_argv


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
