import argparse
import asyncio
import csv

from app.db.connection import SessionLocal, init_db
from app.graph.service import add_influence_with_source


def _parse_row(row: dict) -> dict:
    """Нормализация строки: обязательные from/to/url, остальное — опционально."""
    from_name = (row.get("from") or row.get("от") or "").strip()
    to_name = (row.get("to") or row.get("к") or "").strip()
    url = (row.get("url") or row.get("ссылка") or "").strip()
    if not (from_name and to_name and url):
        raise ValueError("Каждая строка должна содержать from, to и url")

    def _wrap(key: str, default):
        val = row.get(key)
        if val is None:
            return default
        val = str(val).strip()
        return val if val else default

    def _float(key: str, default):
        val = row.get(key)
        if val is None:
            return default
        try:
            return float(val)
        except ValueError:
            return default

    return {
        "from_name": from_name,
        "to_name": to_name,
        "url": url,
        "rationale": _wrap("rationale", ""),
        "strength": _wrap("strength", "medium"),
        "confidence": _float("confidence", 0.7),
        "direction": _wrap("direction", "positive"),
        "kind": _wrap("kind", "direct"),
    }


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Добавить научную/аналитическую ссылку-обоснование к ребру графа (FR-05-08)"
    )
    parser.add_argument(
        "--file",
        default="",
        help="CSV-файл со ссылками; колонки: from,to,url[,rationale,strength,confidence,direction,kind] (поддерж. от/к/ссылка)",
    )
    parser.add_argument("--from", dest="from_name", default="", help="сущность-источник")
    parser.add_argument("--to", dest="to_name", default="", help="сущность-приёмник")
    parser.add_argument("--url", dest="url", default="", help="ссылка на научную/аналитическую работу")
    parser.add_argument("--rationale", default="", help="обоснование связи")
    parser.add_argument("--strength", default="medium", help="сила связи (weak/medium/strong)")
    parser.add_argument("--confidence", type=float, default=0.7, help="уверенность (0..1)")
    parser.add_argument("--direction", default="positive", help="направление (positive/negative)")
    parser.add_argument("--kind", default="direct", help="характер (direct/indirect)")
    parser.add_argument(
        "--pdf",
        default="",
        help="путь к PDF-статье: текст извлекается (pypdf), LLM определяет связь «от->к», ребро добавляется",
    )
    parser.add_argument(
        "--graph",
        default="",
        help="путь к файлу с ASCII/текстовым графом влияния: LLM парсит схему в несколько связей (from/to/direction)",
    )
    args = parser.parse_args()

    await init_db()
    async with SessionLocal() as session:
        records = []
        if args.pdf.strip():
            from pathlib import Path

            from app.graph.pdf_analysis import analyze_pdf_relation

            pdf_path = args.pdf.strip()
            rel = await analyze_pdf_relation(pdf_path)
            if not rel.is_valid:
                print(f"PDF {pdf_path}: не удалось определить связь из текста", flush=True)
                return
            records.append(
                {
                    "from_name": rel.from_name,
                    "to_name": rel.to_name,
                    "url": f"file://{Path(pdf_path).resolve()}",
                    "rationale": rel.rationale,
                    "strength": args.strength,
                    "confidence": rel.confidence,
                    "direction": rel.direction,
                    "kind": args.kind,
                }
            )
        elif args.graph.strip():
            from app.graph.graph_analysis import (
                analyze_graph_text,
                graph_relations_to_csv,
            )

            graph_path = args.graph.strip()
            with open(graph_path, encoding="utf-8") as f:
                graph_text = f.read()
            rels = await analyze_graph_text(graph_text)
            if not rels:
                print(f"Граф {graph_path}: не удалось определить связи из схемы", flush=True)
                return
            csv_text = graph_relations_to_csv(rels)
            reader = csv.DictReader(
                csv_text.splitlines(),
                fieldnames=["from", "to", "url", "rationale", "strength", "confidence", "direction", "kind"],
            )
            next(reader, None)  # пропустить заголовок
            for raw in reader:
                records.append(_parse_row(raw))
        elif args.file.strip():
            with open(args.file, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for raw in reader:
                    records.append(_parse_row(raw))
        elif args.from_name and args.to_name and args.url:
            records.append(
                {
                    "from_name": args.from_name,
                    "to_name": args.to_name,
                    "url": args.url,
                    "rationale": args.rationale,
                    "strength": args.strength,
                    "confidence": args.confidence,
                    "direction": args.direction,
                    "kind": args.kind,
                }
            )
        else:
            parser.error("укажите --file или связку --from --to --url")

        if not records:
            print("Нет записей для добавления")
            return

        created = updated = duplicate = 0
        for r in records:
            res = await add_influence_with_source(session, **r)
            status = res["status"]
            if status == "created":
                created += 1
            elif status == "updated":
                updated += 1
            else:
                duplicate += 1
            print(f"  {status.upper()}: {r['from_name']} -> {r['to_name']} @ {r['url']}")
        await session.commit()
        print(f"Итого: создано {created}, дополнено {updated}, дубликатов {duplicate}")


if __name__ == "__main__":
    asyncio.run(main())
