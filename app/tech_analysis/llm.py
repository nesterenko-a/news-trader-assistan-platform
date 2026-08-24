"""Клиент LLM для «Теханализа в LLM».

По умолчанию используется ChatGPT (CHATGPT_* из конфига). Если `CHATGPT_API_KEY`
пуст — автоматически используется провайдер DeepSeek (LLM_*), если у него задан
ключ. Оба совместимы с OpenAI API, поэтому используем тот же AsyncOpenAI клиент.
"""

from dataclasses import dataclass

from openai import AsyncOpenAI


class ChatGPTClient:
    def __init__(
        self, base_url: str, api_key: str, model: str, timeout: float = 120.0
    ):
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        self.model = model
        self.provider = "deepseek" if "deepseek" in (base_url or "") else "chatgpt"

    async def chat(self, system_prompt: str, user_prompt: str) -> str:
        response = await self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
        )
        return response.choices[0].message.content or ""

    @classmethod
    def from_settings(cls) -> "ChatGPTClient":
        from app.config import get_settings

        settings = get_settings()
        if settings.chatgpt_api_key:
            return cls(
                base_url=settings.chatgpt_base_url,
                api_key=settings.chatgpt_api_key,
                model=settings.chatgpt_model,
                timeout=settings.chatgpt_request_timeout,
            )
        # CHATGPT_API_KEY пуст — fallback на DeepSeek (LLM_*)
        return cls(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            timeout=settings.llm_request_timeout,
        )


@dataclass
class ResolvedLlm:
    api_key: str
    base_url: str
    model: str
    timeout: float


def resolve_llm() -> ResolvedLlm:
    """Определяет провайдера для тех.анализа: ChatGPT или DeepSeek (fallback).

    Возвращает None-значимые поля пустыми, если нет ни одного ключа.
    """
    from app.config import get_settings

    settings = get_settings()
    if settings.chatgpt_api_key:
        return ResolvedLlm(
            api_key=settings.chatgpt_api_key,
            base_url=settings.chatgpt_base_url,
            model=settings.chatgpt_model,
            timeout=settings.chatgpt_request_timeout,
        )
    if settings.llm_api_key:
        return ResolvedLlm(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            timeout=settings.llm_request_timeout,
        )
    return ResolvedLlm(api_key="", base_url="", model="", timeout=0.0)

