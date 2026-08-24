"""Пакет «Теханализ в LLM»: отправка набора данных по бумаге в ChatGPT.

Модули:
- llm — клиент ChatGPT (OpenAI-совместимый).
- parser — извлечение JSON-блока из ответа и заполнение verdict/entry/tp/sl.
- request_builder — сборка Markdown-запроса из данных БД.
- service — запуск, этапы, повтор, нотисы.
"""
