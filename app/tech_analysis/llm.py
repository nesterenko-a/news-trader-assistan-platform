"""Клиент ChatGPT для «Теханализа в LLM».

Использует отдельные настройки CHATGPT_* из конфига (по аналогии с DeepSeek
LLMClient, но с собственными base_url/api_key/model). ChatGPT совместим с
OpenAI API, поэтому используем тот же AsyncOpenAI клиент.
"""

from openai import AsyncOpenAI


class ChatGPTClient:
    def __init__(
        self, base_url: str, api_key: str, model: str, timeout: float = 120.0
    ):
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        self.model = model

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
        return cls(
            base_url=settings.chatgpt_base_url,
            api_key=settings.chatgpt_api_key,
            model=settings.chatgpt_model,
            timeout=settings.chatgpt_request_timeout,
        )
