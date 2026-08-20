import argparse
import asyncio
import sys

from app.db.connection import SessionLocal, init_db
from app.graph.service import export_graph_records, graph_to_jsonl


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

    payload = graph_to_jsonl(entities, influences)
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
