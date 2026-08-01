# AGENTS.md

Файл для AI-агентов, работающих с проектом **NewsTrader Assistant** — ассистент для торговли ценными бумагами на основе новостного фона и цепочек рыночных связей (knowledge graph).

Полная спецификация проекта — в `docs/` (начните с `docs/README.md`). Правила работы — в `docs/16-working-process.md` — **обязательны к выполнению**.

## Стек

Python 3.13 · FastAPI · SQLAlchemy 2 (async) · PostgreSQL 13.3 (Docker) · Liquibase · DeepSeek LLM (OpenAI-совместимый) · MOEX ISS · python-telegram-bot · Jinja2. Windows + PowerShell.

## Запуск и проверки

Все команды — из корня проекта, интерпретатор `.venv\Scripts\python.exe` (Windows).

| Действие | Команда |
|---|---|
| Тесты | `.venv\Scripts\python.exe -m pytest -q` |
| Проверка компиляции | `.venv\Scripts\python.exe -m compileall -q app scripts` |
| Смоук API и веба | `.venv\Scripts\python.exe -m scripts.smoke` |
| Запуск (веб + Telegram-бот) | `.venv\Scripts\python.exe -m scripts.run_app` |
| Только веб | `.venv\Scripts\python.exe -m uvicorn app.main:app` |
| Поднять БД | `docker compose -f docker/docker-compose.yml up -d db` |
| Миграции | `docker compose -f docker/docker-compose.yml up migrations` |
| Наполнить справочники | `.venv\Scripts\python.exe -m scripts.seed_db` |
| Ежедневный конвейер | `.venv\Scripts\python.exe -m scripts.daily_pipeline` |
| Обновить цены | `.venv\Scripts\python.exe -m scripts.update_prices --days N` (или `--from YYYY-MM-DD`) |
| Калибровка порогов | `.venv\Scripts\python.exe -m scripts.calibrate` |
| Бэктест | `.venv\Scripts\python.exe -m scripts.backtest` |
| Бэктест «на момент T» | `.venv\Scripts\python.exe -m scripts.backtest_asof` (опции `--tickers --start --end --horizon --step`) |

## Структура

- `app/` — основной код: `api/`, `bot/`, `collectors/`, `db/`, `graph/`, `llm/`, `market/`, `news/`, `presentation/`, `strategy/`, `web/`
- `scripts/` — утилиты и лаунчеры
- `tests/` — pytest (на SQLite)
- `docs/` — документация (версионируется)
- `liquibase/` — ченджлоги БД
- `docker/` — docker-compose (Postgres + Liquibase)

## Конвенции

- Язык: русский (интерфейс, документация, сообщения). Код-идентификаторы — английский.
- Комментарии в коде не добавляем, если об этом не просят.
- Всё асинхронное: SQLAlchemy async, асинхронные хендлеры.
- Конфигурация — `app/config.py` (pydantic-settings) + `.env`; секреты в git не попадают (`.env` в `.gitignore`).
- Схема БД управляется миграциями Liquibase: новый файл `liquibase/changelogs/NNN_*.xml` + включение в `changelog-master.xml`.
- Презентация (web + Telegram) — через слой `app/presentation` (общая фабрика `StrategyView`), без дублирования форматирования.

## Правила работы (обязательно)

1. После изменения кода — прогнать тесты и compileall.
2. После изменения кода — обновить документацию, если затронуто описываемое поведение, и поднять версии затронутых документов (см. `docs/16-working-process.md`).
3. Коммитить в git осмысленными сообщениями: `type(scope): summary`.
4. Стабильные версии отмечать аннотированным git-тегом.
5. Секреты не коммитить.
