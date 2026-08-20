import json
import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from app.llm.analyzer import _strip_json
from app.llm.client import LLMClient

RELATION_SYSTEM_PROMPT = (
    "Ты — финансовый аналитик. По тексту научной/аналитической статьи определи "
    "влияние одной экономической сущности на другую (связь для knowledge graph). "
    "Верни строго JSON без пояснений и markdown:\n"
    '{"from_name": "сущность-источник", '
    '"to_name": "сущность-приёмник", '
    '"direction": "positive|negative", '
    '"rationale": "краткое обоснование на русском (1 фраза)", '
    '"confidence": 0.0-1.0}\n'
    "Учитывай типы сущностей из графа: company, sector, commodity, currency, index, "
    "macro_indicator, event, region. Если связей не видно — верни пустые from_name/to_name."
)


@dataclass
class PdfRelation:
    from_name: str = ""
    to_name: str = ""
    direction: str = "positive"
    rationale: str = ""
    confidence: float = 0.0

    @property
    def is_valid(self) -> bool:
        return bool(self.from_name and self.to_name and self.confidence >= 0.5)


def extract_pdf_text(path: str | Path, max_chars: int = 12000) -> str:
    reader = PdfReader(str(path))
    pages = []
    for page in reader.pages[:40]:
        text = page.extract_text() or ""
        if text:
            pages.append(text)
    joined = "\n".join(pages)
    return joined[:max_chars]


def parse_relation(raw: str) -> PdfRelation:
    try:
        data = json.loads(_strip_json(raw))
    except json.JSONDecodeError:
        return PdfRelation()
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return PdfRelation(
        from_name=(data.get("from_name") or "").strip(),
        to_name=(data.get("to_name") or "").strip(),
        direction=(data.get("direction") or "positive").strip(),
        rationale=(data.get("rationale") or "").strip(),
        confidence=confidence,
    )


async def analyze_pdf_relation(path: str | Path, client: LLMClient | None = None) -> PdfRelation:
    """Извлекает текст из PDF и через LLM определяет связь «от → к».

    Полученная связь затем используется для add_influence_with_source.
    """
    text = extract_pdf_text(path)
    if not text:
        return PdfRelation()
    client = client or LLMClient.from_settings()
    user_prompt = f"Текст статьи (обрыв):\n\n{text}"
    raw = await client.chat(RELATION_SYSTEM_PROMPT, user_prompt)
    return parse_relation(raw)
