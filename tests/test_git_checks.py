import sys

import pytest

from tools import git_checks


@pytest.mark.parametrize("filename", [
    ".env", ".env.production", "config/.env.local", "logs/run.log",
    ".venv/Scripts/python.exe", "app/__pycache__/config.cpython-313.pyc",
    "smoke.db", "smoke.db-wal", "data.sqlite3", "auth.session",
    "auth.session-journal", "node_modules/lib/index.js", "test-results/trace.zip",
    "uploads/avatar.png", "app\\__pycache__\\config.pyc",
])
def test_forbidden_artifacts(filename):
    assert git_checks.forbidden_path(filename)


@pytest.mark.parametrize("filename", [
    ".env.example", "app/config.py", "docs/13-operations.md",
    "tests/test_migrations.py", ".github/workflows/tests.yml", ".secrets.baseline",
])
def test_source_files_allowed(filename):
    assert not git_checks.forbidden_path(filename)


@pytest.mark.parametrize("subject, expected", [
    ("infra(git): добавить hooks", 0),
    ("tasks(process): define workflow", 0),
    ("spec(process): document workflow", 0),
    ("feat(api): добавить метод", 0),
    ("refactor(core): упростить обработку", 0),
    ("docs(process): обновить правила", 0),
    ("test(git): проверить сообщения", 0),
    ("chore(deps): обновить зависимости", 0),
    ("fix(admin): обновить карточку\n\nОписание изменения", 0),
    ("docs: обновить регламент", 1),
    ("feat(): новая функция", 1),
    ("fix(api): ", 1),
    ("unknown(api): описание", 1),
    ("tasks: define workflow", 1),
    ("spec(): document workflow", 1),
    ("tasks(process): ", 1),
    ("spec(process):\t", 1),
    ("spec(process) document workflow", 1),
    ("tasks(process):define workflow", 1),
    ("", 1),
])
def test_commit_message(tmp_path, subject, expected):
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text(subject, encoding="utf-8-sig")
    assert git_checks.check_commit_message(str(message)) == expected


def test_python_checks_fail_without_project_venv(tmp_path, monkeypatch):
    monkeypatch.setattr(git_checks, "ROOT", tmp_path)
    assert git_checks.check_python() == 1


@pytest.mark.skipif(sys.platform == "win32", reason="CI fallback uses Linux Python")
def test_openapi_contract_uses_ci_python_without_project_venv(tmp_path, monkeypatch):
    calls = []

    def succeed(command, **kwargs):
        calls.append(command)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(git_checks, "ROOT", tmp_path)
    monkeypatch.setattr(git_checks.subprocess, "run", succeed)
    assert git_checks.check_openapi_contract() == 0
    assert calls[0][0] == sys.executable


def test_python_checks_stop_on_failure(tmp_path, monkeypatch):
    python = tmp_path / ".venv" / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )
    python.parent.mkdir(parents=True)
    python.touch()
    calls = []

    def fail(command, **kwargs):
        calls.append(command)
        return type("Result", (), {"returncode": 2})()

    monkeypatch.setattr(git_checks, "ROOT", tmp_path)
    monkeypatch.setattr(git_checks.subprocess, "run", fail)
    assert git_checks.check_python() == 2
    assert len(calls) == 1
