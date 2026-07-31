import json
import re
from dataclasses import dataclass, field

from app.llm.client import LLMClient

ANALYSIS_SYSTEM_PROMPT = (
    "Ты — финансовый аналитик. Твоя задача — разобрать новость/статью и вернуть результат "
    "строго в виде JSON без пояснений и markdown-разметки.\n"
    "Формат ответа:\n"
    "{\n"
    '  "topic": "macro|sector|company|geopolitics|regulation|other",\n'
    '  "summary": "краткое резюме новости на русском",\n'
    '  "entities": [\n'
    '    {\n'
    '      "name": "название сущности",\n'
    '      "type": "company|sector|commodity|currency|index|macro_indicator|region|person|event",\n'
    '      "sentiment": "positive|negative|neutral",\n'
    '      "impact": 0.5,\n'
    '      "snippet": "фраза из текста с упоминанием",\n'
    '      "role": "primary|secondary"\n'
    "    }\n"
    "  ],\n"
    '  "facts": ["ключевой факт или цифра"],\n'
    '  "confidence": 0.9\n'
    "}\n"
    "impact — сила влияния новости на сущность от 0 до 1. "
    "Если уверенности в анализе мало, ставь confidence ниже 0.5."
)


@dataclass
class EntityAnalysis:
    name: str = ""
    type: str = "other"
    sentiment: str = "neutral"
    impact: float = 0.0
    snippet: str = ""
    role: str = "secondary"


@dataclass
class ArticleAnalysis:
    topic: str = "other"
    summary: str = ""
    entities: list[EntityAnalysis] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    confidence: float = 0.0

    @property
    def is_reliable(self) -> bool:
        return self.confidence >= 0.5


def _strip_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def parse_analysis(raw: str) -> ArticleAnalysis:
    try:
        data = json.loads(_strip_json(raw))
    except json.JSONDecodeError:
        return ArticleAnalysis()

    entities = []
    for item in data.get("entities", []):
        entities.append(
            EntityAnalysis(
                name=item.get("name", ""),
                type=item.get("type", "other"),
                sentiment=item.get("sentiment", "neutral"),
                impact=float(item.get("impact", 0.0)),
                snippet=item.get("snippet", ""),
                role=item.get("role", "secondary"),
            )
        )

    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    return ArticleAnalysis(
        topic=data.get("topic", "other"),
        summary=data.get("summary", ""),
        entities=entities,
        facts=[str(f) for f in data.get("facts", [])],
        confidence=confidence,
    )


class ArticleAnalyzer:
    def __init__(self, client: LLMClient):
        self._client = client

    async def analyze(self, title: str, text: str) -> ArticleAnalysis:
        user_prompt = f"Заголовок: {title}\n\nТекст:\n{text[:8000]}"
        raw = await self._client.chat(ANALYSIS_SYSTEM_PROMPT, user_prompt)
        return parse_analysis(raw)
