import json
from dataclasses import dataclass, asdict

from app.llm.analyzer import _strip_json
from app.llm.client import LLMClient

GRAPH_SYSTEM_PROMPT = (
    "Ты — финансовый аналитик. По ASCII/текстовой схеме влияния экономических "
    "сущностей (knowledge graph) определи все направленные связи «от → к». "
    "Верни строго JSON-массив без пояснений и markdown:\n"
    '[{"from": "сущность-источник", "to": "сущность-приёмник", '
    '"direction": "positive|negative", "rationale": "краткое обоснование на русском (1 фраза)"}, ...]\n'
    "Правила: узлы — сущности (company, sector, commodity, currency, index, "
    "macro_indicator, event, region); стрелка ↧ указывает направление влияния "
    "от источника к приёмнику; метки ↑/↓ или «positive/negative» при узле "
    "интерпретируй как знак влияния (↑/positive — положительное, ↓/negative — "
    "отрицательное); если знак не указан — direction=positive. Имя сущности "
    "бери как на схеме, без эмодзи/декораций (например OIL, GAS, WALMART). "
    "Если связей нет — верни пустой массив []."
)


@dataclass
class GraphRelation:
    from_name: str = ""
    to_name: str = ""
    direction: str = "positive"
    rationale: str = ""

    @property
    def is_valid(self) -> bool:
        return bool(self.from_name and self.to_name)


def parse_graph_relations(raw: str) -> list[GraphRelation]:
    """Парсинг JSON-массива связей из ответа LLM (чистый, тестируемый)."""
    if not raw or not raw.strip():
        return []
    try:
        data = json.loads(_strip_json(raw))
    except json.JSONDecodeError:
        # пробуем «обернуть в массив», если LLM вернул один объект
        try:
            data = [json.loads(_strip_json(raw))]
        except json.JSONDecodeError:
            return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    result = []
    for item in data:
        rel = GraphRelation(
            from_name=(item.get("from") or "").strip(),
            to_name=(item.get("to") or "").strip(),
            direction=(item.get("direction") or "positive").strip(),
            rationale=(item.get("rationale") or "").strip(),
        )
        if rel.is_valid:
            result.append(rel)
    return result


async def analyze_graph_text(text: str, client: LLMClient | None = None) -> list[GraphRelation]:
    """Отправить ASCII-граф в LLM и получить список связей.

    Своего рода аналог analyze_pdf_relation, но для произвольной текстовой схемы.
    """
    if not text or not text.strip():
        return []
    client = client or LLMClient.from_settings()
    user_prompt = f"ASCII/граф-схема влияния:\n\n{text}"
    raw = await client.chat(GRAPH_SYSTEM_PROMPT, user_prompt)
    return parse_graph_relations(raw)


def graph_relations_to_csv(relations: list[GraphRelation]) -> str:
    """Сериализовать связи в CSV-строку в формате add_research --file.

    Колонки: from,to,url,rationale,strength,confidence,direction,kind.
    source_ref ставится 'curated' через пустой url (далее точечно).
    """
    import csv
    import io

    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=["from", "to", "url", "rationale", "strength", "confidence", "direction", "kind"],
    )
    writer.writeheader()
    for r in relations:
        writer.writerow(
            {
                "from": r.from_name,
                "to": r.to_name,
                "url": "curated",
                "rationale": r.rationale,
                "strength": "medium",
                "confidence": 0.7,
                "direction": r.direction,
                "kind": "direct",
            }
        )
    return buf.getvalue()


def graph_relations_dump(relations: list[GraphRelation]) -> str:
    return json.dumps([asdict(r) for r in relations], ensure_ascii=False)
