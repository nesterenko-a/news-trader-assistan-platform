import argparse
import json
from pathlib import Path
import sys

from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "openapi" / "openapi.json"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api.router import api_router
from app.main import app


def rendered_openapi() -> str:
    api = FastAPI(title=app.title, version=app.version)
    api.include_router(api_router)
    return json.dumps(api.openapi(), ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    rendered = rendered_openapi()
    if args.check:
        if output.is_file() and output.read_text(encoding="utf-8") == rendered:
            return 0
        print("OpenAPI-контракт не синхронизирован. Выполните: .venv\\Scripts\\python.exe tools/export_openapi.py")
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
