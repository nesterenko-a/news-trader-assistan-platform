import argparse
import asyncio
import json
import sys

from app.db.connection import SessionLocal, init_db
from app.graph.service import export_graph_records


def _dump_jsonl(entities: list[dict], influences: list[dict]) -> str:
    lines: list[str] = []
    for e in entities:
        lines.append(json.dumps({"record": "entity", **e}, ensure_ascii=False))
    for i in influences:
        lines.append(json.dumps({"record": "influence", **i}, ensure_ascii=False))
    return "\n".join(lines) + "\n"


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Экспорт графа (сущности и рёбра) в JSONL для переноса/резервной копии"
    )
    parser.add_argument(
        "--out",
        default="",
        help="файл для записи дампа; если не указан — вывод в stdout",
    )
    args = parser.parse_args()

    await init_db()
    async with SessionLocal() as session:
        entities, influences = await export_graph_records(session)

    payload = _dump_jsonl(entities, influences)
    if args.out.strip():
        with open(args.out.strip(), "w", encoding="utf-8") as f:
            f.write(payload)
        print(f"Граф выгружен: сущностей {len(entities)}, рёбер {len(influences)} -> {args.out.strip()}")
    else:
        sys.stdout.write(payload)
        print(
            f"# сущностей {len(entities)}, рёбер {len(influences)} (JSONL выше)",
            file=sys.stderr,
        )


if __name__ == "__main__":
    asyncio.run(main())
