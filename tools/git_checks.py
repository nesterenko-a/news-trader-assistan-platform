import argparse
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
COMMIT_PATTERN = re.compile(
    r"(?:feat|fix|refactor|docs|infra|test|chore)\([a-zA-Z0-9][a-zA-Z0-9._/-]*\): \S.*"
)
FORBIDDEN_DIRS = {
    ".venv", "venv", "__pycache__", "logs", "node_modules", ".pytest_cache",
    ".ruff_cache", "test-results", "playwright-report", "uploads",
}


def forbidden_path(filename: str) -> bool:
    path = PurePosixPath(filename.replace("\\", "/").lower())
    name = path.name
    return (
        bool(FORBIDDEN_DIRS.intersection(path.parts))
        or name == ".env"
        or (name.startswith(".env.") and name != ".env.example")
        or name.endswith((".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3",
                          ".pyc", ".pyo", ".session", ".session-journal"))
        or name == ".coverage"
    )


def check_artifacts(filenames: list[str]) -> int:
    forbidden = [filename for filename in filenames if forbidden_path(filename)]
    for filename in forbidden:
        print(f"Запрещённый файл в коммите: {filename}")
    return int(bool(forbidden))


def check_commit_message(filename: str) -> int:
    lines = Path(filename).read_text(encoding="utf-8-sig").splitlines()
    subject = lines[0] if lines else ""
    if COMMIT_PATTERN.fullmatch(subject):
        return 0
    print("Сообщение коммита должно иметь формат type(scope): summary.")
    print("Типы: feat, fix, refactor, docs, infra, test, chore.")
    return 1


def project_python() -> Path | None:
    python = ROOT / ".venv" / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )
    if not python.is_file():
        print("Не найден Python в .venv. Подготовьте окружение по docs/13-operations.md §7.")
        return None
    return python


def check_python() -> int:
    python = project_python()
    if python is None:
        return 1
    for arguments in (
        ["-m", "compileall", "-q", "app", "scripts", "tools", "tests"],
        ["-m", "pytest", "-q"],
    ):
        result = subprocess.run([str(python), *arguments], cwd=ROOT)
        if result.returncode:
            return result.returncode
    return 0


def check_openapi_contract() -> int:
    python = project_python()
    if python is None:
        return 1
    return subprocess.run(
        [str(python), "tools/export_openapi.py", "--check"], cwd=ROOT
    ).returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("check", choices=["artifacts", "commit-msg", "python", "openapi"])
    parser.add_argument("filenames", nargs="*")
    args = parser.parse_args()
    if args.check == "artifacts":
        return check_artifacts(args.filenames)
    if args.check == "commit-msg":
        if len(args.filenames) != 1:
            parser.error("commit-msg ожидает один файл сообщения")
        return check_commit_message(args.filenames[0])
    if args.check == "openapi":
        return check_openapi_contract()
    return check_python()


if __name__ == "__main__":
    raise SystemExit(main())
