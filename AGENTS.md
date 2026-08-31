# AGENTS.md

Файл для AI-агентов, работающих с проектом **NewsTrader Assistant** — ассистент для торговли ценными бумагами на основе новостного фона и цепочек рыночных связей (knowledge graph).

Полная спецификация проекта — в `docs/` (начните с `docs/README.md`; комплект из 25 документов, текущая версия v1.150). Правила работы — в `docs/16-working-process.md` — **обязательны к выполнению**.

## Стек

Python 3.13+ (в `.venv` — 3.13.7) · FastAPI · SQLAlchemy 2 (async) · PostgreSQL 13.3 (Docker) · Liquibase · DeepSeek LLM (OpenAI-совместимый) · MOEX ISS · python-telegram-bot · telethon · Jinja2. Windows + PowerShell. Тесты — pytest (`asyncio_mode=auto`) на SQLite.

## Запуск и проверки

Все команды — из корня проекта, интерпретатор `.venv\Scripts\python.exe` (Windows). Быстрые лаунчеры — `.bat`-файлы в `scripts/`.

| Действие | Команда |
|---|---|
| Тесты | `.venv\Scripts\python.exe -m pytest -q` (unit; e2e исключены маркером) |
| E2E-тесты веб-интерфейса | `npx playwright test` (Playwright Test Runner, TS; UI-режим `--ui`; см. `docs/21-web-e2e-tests.md`) |
| Быстрый запуск e2e | `scripts\run_e2e.bat` (лаунчер, аргументы пробрасываются) |
| Проверка компиляции | `.venv\Scripts\python.exe -m compileall -q app scripts` |
| Смоук API и веба | `.venv\Scripts\python.exe -m scripts.smoke` |
| Запуск (веб + Telegram-бот) | `.venv\Scripts\python.exe -m scripts.run_app` |
| Только веб | `.venv\Scripts\python.exe -m uvicorn app.main:app` |
| Только бот | `.venv\Scripts\python.exe -m scripts.run_bot` |
| Поднять БД | `docker compose -f docker/docker-compose.yml up -d db` |
| Миграции | `docker compose -f docker/docker-compose.yml up --build migrations` |
| Наполнить справочники | `.venv\Scripts\python.exe -m scripts.seed_db` |
| Наполнить макро-справочник | `.venv\Scripts\python.exe -m scripts.seed_macro` |
| Собрать новости | `.venv\Scripts\python.exe -m scripts.collect_news` |
| Ежедневный конвейер | `.venv\Scripts\python.exe -m scripts.daily_pipeline` |
| Обработать алерты | `.venv\Scripts\python.exe -m scripts.process_alerts` |
| Обновить цены | `.venv\Scripts\python.exe -m scripts.update_prices --days N` (или `--from YYYY-MM-DD`) |
| Обновить OI фьючерсов | `.venv\Scripts\python.exe -m scripts.update_oi --ticker SECID --days N` (или `--all`, `--from YYYY-MM-DD`) |
| Калибровка порогов | `.venv\Scripts\python.exe -m scripts.calibrate` |
| Калибровка весов факторов | `.venv\Scripts\python.exe -m scripts.calibrate_weights` |
| Бэктест | `.venv\Scripts\python.exe -m scripts.backtest` |
| Бэктест «на момент T» | `.venv\Scripts\python.exe -m scripts.backtest_asof` (опции `--tickers --start --end --horizon --step`) |
| Создать пользователя | `.venv\Scripts\python.exe -m scripts.create_user` |
| Telegram-вход (session) | `.venv\Scripts\python.exe -m scripts.telegram_login` |

## Структура

- `app/` — основной код:
  - `api/`, `web/` — REST API и веб-интерфейс (FastAPI, Jinja2, шаблоны в `web/templates`); `web/middleware.py` — DatabaseGuardMiddleware
  - `bot/` — Telegram-бот
  - `collectors/`, `news/`, `llm/`, `market/` — сбор данных, анализ новостей, DeepSeek LLM, MOEX ISS (`market/` включает `indicators/` — реестр индикаторов `registry.py` + Volume Profile; `oi_data.py` — данные OI, лежит в `market/`)
  - `graph/`, `strategy/`, `macro/` — knowledge graph, движок стратегий, макро-индикаторы
  - `alerts/`, `notices/`, `feedback/`, `paper/` — алерты цен, монитор здоровья (Attention), обратная связь, бумажная торговля
  - `presentation/` — общая фабрика `StrategyView` (web + Telegram)
  - `admin/` — админ-панель: роли (`roles.py`), раннер скриптов (`runner.py`, список `SCRIPTS`)
  - `db/` — SQLAlchemy-модели; `schemas/` — Pydantic-схемы; `config.py` — pydantic-settings
- `scripts/` — утилиты и лаунчеры (+ `.bat`)
- `tests/` — pytest (SQLite)
- `docs/` — документация (версионируется, v1.133)
- `liquibase/` — ченджлоги БД (`changelogs/NNN_*.xml` + `changelog-master.xml`)
- `docker/` — docker-compose (Postgres + Liquibase)

## Конвенции

- Язык: русский (интерфейс, документация, сообщения). Код-идентификаторы — английский.
- Комментарии в коде не добавляем, если об этом не просят.
- Всё асинхронное: SQLAlchemy async, асинхронные хендлеры.
- Конфигурация — `app/config.py` (pydantic-settings) + `.env`; секреты в git не попадают (`.env` в `.gitignore`).
- Схема БД управляется миграциями Liquibase: новый файл `liquibase/changelogs/NNN_*.xml` + включение в `changelog-master.xml`; модель SQLAlchemy меняется только вместе с миграцией.
- Презентация (web + Telegram) — через слой `app/presentation` (общая фабрика `StrategyView`), без дублирования форматирования.

## Правила работы (обязательно)

1. После изменения кода — прогнать тесты и compileall.
   При изменении веб-интерфейса (шаблоны, роуты, поведение страниц) — обязательно актуализировать e2e-тесты в `tests/e2e/` (добавить для новой функциональности, изменить затронутые, удалить для убранной) и прогнать `npx playwright test`.
2. После изменения кода — обновить документацию, если затронуто описываемое поведение, и поднять версии затронутых документов (см. `docs/16-working-process.md`).
3. Коммитить в git осмысленными сообщениями: `type(scope): summary`; по одной задаче на коммит; артефакты (`__pycache__`, `*.db`, `logs/`) не коммитить.
4. Стабильные версии отмечать аннотированным git-тегом.
5. Секреты не коммитить.
6. Перед реализацией задачи из дорожной карты проверить достаточность документации по ней (обоснование, FR/UC, модель данных, API, UI); при нехватке — зафиксировать дизайн в `docs/` и показать пользователю на подтверждение; реализация начинается только после согласования (правки вносятся по замечаниям).
7. После завершения задачи — задокументировать, как пользоваться результатом: что запускать, где появился функционал, команды/API (13-operations, 14-web-interface, 10-api-specification, 17-quickstart) и обновить статус в 12-roadmap.
8. Новые скрипты в `scripts/` обязательно регистрировать в админ-панели веб-интерфейса (`app/admin/runner.py`, список `SCRIPTS`) с описанием назначения и периодичности запуска.

## Notes

- Документация: комплект **v1.150**; актуальные расширенные версии: 09 — v1.17, 10 — v1.29, 12 — v1.54, 13 — v1.56, 14 — v1.88, 18 — v1.2, 19 — v1.23, 23 — реализовано v1.10, 24 — реализовано v1.4, 25 — реализовано v1.15; остальные см. в шапках документов.
- Roadmap (12): этап 0 завершён; этап 1 реализован за исключением английского языка и глубокого бэкфилла исторических новостей; этап 2 начат. Реализованы paper trading, контраргументы/риски, веса факторов, научные источники в evidence-цепочках, импорт/экспорт и карта knowledge graph, OI и клиентские группы, Volume Profile, поддержка/сопротивление, EMA/MACD, Bollinger Bands, ATR, ADX/DI, RSI, базис фьючерс–спот, E2E веб-интерфейса, **Теханализ в LLM**, **реальное время** и **Top-5 сделок**. Открытые задачи этапа 2: расширение и актуализация knowledge graph, автоматическое обнаружение связей с кураторским подтверждением; низкоприоритетный TODO Top-5 — рабочий фильтр качества сделки.
- `/indicators` строится из реестра `app/market/indicators/registry.py`: новый индикатор в реестре автоматически получает вкладку.
- E2E-тесты (`tests/e2e/`, Playwright Test Runner, TypeScript) запускаются отдельным прогоном `npx playwright test` (UI-режим `--ui`); в `pytest -q` не входят; расхождения, найденные e2e, фиксируются в реестре док. 21 (§6).
