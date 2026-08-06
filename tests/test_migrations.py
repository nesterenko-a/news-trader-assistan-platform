from app.db.migrations import run_migrations


def test_migrations_skipped_for_sqlite(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.db.migrations.subprocess.run", lambda *a, **k: calls.append(a) or None
    )
    assert run_migrations("sqlite+aiosqlite:///:memory:") is True
    assert calls == []


def test_migrations_run_for_postgres(monkeypatch):
    class Proc:
        returncode = 0

    calls = []
    monkeypatch.setattr(
        "app.db.migrations.subprocess.run",
        lambda *a, **k: calls.append(a) or Proc(),
    )
    assert run_migrations("postgresql+asyncpg://u:p@h/db") is True
    assert calls
    cmd = calls[0][0]
    assert "docker" in cmd and "migrations" in cmd and "docker/docker-compose.yml" in cmd


def test_migrations_warn_on_failure(monkeypatch):
    class Proc:
        returncode = 1

    monkeypatch.setattr(
        "app.db.migrations.subprocess.run", lambda *a, **k: Proc()
    )
    assert run_migrations("postgresql+asyncpg://u:p@h/db") is False


def test_migrations_warn_when_docker_missing(monkeypatch):
    def _raise(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr("app.db.migrations.subprocess.run", _raise)
    assert run_migrations("postgresql+asyncpg://u:p@h/db") is False
