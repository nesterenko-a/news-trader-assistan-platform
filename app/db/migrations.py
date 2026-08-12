"""Автозапуск миграций Liquibase при старте приложения (только для PostgreSQL)."""

import subprocess

from app.config import get_settings

_COMPOSE_FILE = "docker/docker-compose.yml"
_MIGRATIONS_SERVICE = "migrations"


def run_migrations(database_url: str | None = None) -> bool:
    """Проверяет и применяет миграции Liquibase при старте приложения.

    Для PostgreSQL запускает `docker compose -f docker/docker-compose.yml up
    --build migrations` (идемпотентно: Liquibase применяет только новые
    changesets и сам поднимает контейнер БД из-за depends_on; `--build`
    пересобирает образ с ченджлогами — кэш слоёв делает это быстрым, когда
    ничего не менялось). Для SQLite/прочих — пропускает
    (схема создаётся через create_all). Логирует в stdout приложения.

    Возвращает True при успехе или когда миграции не требуются; False — если
    docker недоступен или миграции завершились ошибкой (приложение продолжает
    запуск с предупреждением).
    """
    url = database_url or get_settings().database_url
    if not url.startswith("postgres"):
        print(
            "[migrations] БД не PostgreSQL — миграции Liquibase не требуются "
            "(схема через create_all)",
            flush=True,
        )
        return True

    print(
        "[migrations] Проверка миграций Liquibase (PostgreSQL)...",
        flush=True,
    )
    try:
        proc = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                _COMPOSE_FILE,
                "up",
                "--build",
                _MIGRATIONS_SERVICE,
            ]
        )
    except FileNotFoundError:
        print(
            "[migrations] ПРЕДУПРЕЖДЕНИЕ: docker недоступен — миграции не "
            "применены. Запустите вручную: docker compose -f "
            f"{_COMPOSE_FILE} up --build {_MIGRATIONS_SERVICE}",
            flush=True,
        )
        return False

    if proc.returncode != 0:
        print(
            f"[migrations] ПРЕДУПРЕЖДЕНИЕ: миграции завершились с кодом "
            f"{proc.returncode}. Запустите вручную: docker compose -f "
            f"{_COMPOSE_FILE} up --build {_MIGRATIONS_SERVICE}",
            flush=True,
        )
        return False

    print("[migrations] Миграции проверены и применены (Liquibase: успешно)", flush=True)
    return True
