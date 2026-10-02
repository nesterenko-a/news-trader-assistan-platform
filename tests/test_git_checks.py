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
    ("fix(admin): обновить карточку\n\nОписание изменения", 0),
    ("docs: обновить регламент", 1),
    ("feat(): новая функция", 1),
    ("fix(api): ", 1),
    ("unknown(api): описание", 1),
    ("", 1),
])
def test_commit_message(tmp_path, subject, expected):
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text(subject, encoding="utf-8-sig")
    assert git_checks.check_commit_message(str(message)) == expected


def test_python_checks_fail_without_project_venv(tmp_path, monkeypatch):
    monkeypatch.setattr(git_checks, "ROOT", tmp_path)
    assert git_checks.check_python() == 1


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
