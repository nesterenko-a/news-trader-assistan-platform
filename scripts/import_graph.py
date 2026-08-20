import argparse
import asyncio
import json

from app.db.connection import SessionLocal, init_db
from app.graph.service import import_graph_records


def _read_records(path: str) -> tuple[list[dict], list[dict]]:
    entities: list[dict] = []
    influences: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            kind = obj.get("record")
            if kind == "entity":
                entities.append({k: v for k, v in obj.items() if k != "record"})
            elif kind == "influence":
                influences.append({k: v for k, v in obj.items() if k != "record"})
            else:
                raise ValueError(f"Неизвестный record записи: {kind}")
    return entities, influences


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Импорт графа (сущности и рёбра) из JSONL, сгенерированного export_graph"
    )
    parser.add_argument("--file", required=True, help="путь к JSONL-файлу экспорта")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="только показать план (не записывать в БД)",
    )
    args = parser.parse_args()

    entities, influences = _read_records(args.file)
    await init_db()

    counts = {"created_e": 0, "touched_e": 0, "created_i": 0, "skipped_i": 0, "merged_i": 0}

    async with SessionLocal() as session:
        if args.dry_run:
            counts["skipped_i"] = len(influences)
        else:
            counts = await import_graph_records(session, entities, influences)
            await session.commit()

    print(
        f"Импорт: сущностей создано {counts['created_e']}, дополнено {counts['touched_e']}, "
        f"рёбер создано {counts['created_i']}, пропущено {counts['skipped_i']}, "
        f"дополнено ссылок {counts['merged_i']}" + (" (dry-run)" if args.dry_run else "")
    )


if __name__ == "__main__":
    asyncio.run(main())
