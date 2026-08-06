"""LLM-разбор лент новостей: извлечение записей из произвольного контента
(RSS/HTML), который не смогли распарсить штатные парсеры (fallback по галочке
«LLM-разбор» у источника)."""

import json
import re

from app.llm.client import LLMClient

MAX_LLM_BYTES = 12000

_LLM_EXTRACT_PROMPT = (
    "Ты — парсер новостных лент. Из фрагмента ниже извлеки новостные записи "
    "(RSS/Atom XML, HTML со списком новостей или любой другой вид). "
    "Верни ТОЛЬКО JSON-массив без пояснений и без markdown-разметки: "
    '[{"title": "...", "link": "https://...", "description": "...", '
    '"published": "..."}]. Если новостных записей в фрагменте нет — верни []. '
    "Заголовки обрезай до 300 символов, description — до 2000."
)


def _extract_json_list(raw: str) -> list[dict]:
    """Достаёт JSON-массив из ответа LLM (устойчиво к markdown/тексту вокруг)."""
    if not raw:
        return []
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        out.append(
            {
                "title": title[:300],
                "link": str(item.get("link") or "").strip(),
                "description": str(item.get("description") or "")[:2000],
                "published": str(item.get("published") or "").strip(),
            }
        )
    return out


async def parse_feed_with_llm(data: bytes) -> list[dict]:
    """Извлекает записи ленты через LLM (DeepSeek). Возвращает [] при ошибке."""
    text = data.decode("utf-8", "replace")[:MAX_LLM_BYTES]
    if not text.strip():
        return []
    client = LLMClient.from_settings()
    raw = await client.chat(_LLM_EXTRACT_PROMPT, text)
    return _extract_json_list(raw)
