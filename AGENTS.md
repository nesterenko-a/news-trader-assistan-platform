# AGENTS.md

Файл для AI-агентов, работающих с проектом **NewsTrader Assistant** — ассистент для торговли ценными бумагами на основе новостного фона и цепочек рыночных связей (knowledge graph).

Полная спецификация проекта — в `docs/`: перечень, назначение и актуальная версия комплекта — в `docs/README.md` (единственный источник этих данных — не копировать их сюда). Архитектура документации и карта «кто за что отвечает» — в `docs/32-docs-architecture.md`. Правила работы — в `docs/16-working-process.md` — **обязательны к выполнению**.

## Стек

Python 3.13+ (в `.venv` — 3.13.7) · FastAPI · SQLAlchemy 2 (async) · PostgreSQL 13.3 (Docker) · Liquibase · DeepSeek LLM (OpenAI-совместимый) · MOEX ISS · python-telegram-bot · telethon · Jinja2. Windows + PowerShell. Тесты — pytest (`asyncio_mode=auto`) на SQLite.

## Запуск и проверки

Все команды — из корня проекта, интерпретатор `.venv\Scripts\python.exe` (Windows). Быстрые лаунчеры — `.bat`-файлы в `scripts/`.

Минимальный цикл разработчика:

| Действие | Команда |
|---|---|
| Тесты | `.venv\Scripts\python.exe -m pytest -q` (unit; e2e исключены маркером) |
| E2E-тесты веб-интерфейса | `npx playwright test` (Playwright Test Runner, TS; UI-режим `--ui`; см. `docs/21-web-e2e-tests.md`) |
| Проверка компиляции | `.venv\Scripts\python.exe -m compileall -q app scripts` |
| Смоук API и веба | `.venv\Scripts\python.exe -m scripts.smoke` |
| Запуск (веб + Telegram-бот) | `.venv\Scripts\python.exe -m scripts.run_app` |
| Поднять БД / миграции | `docker compose -f docker/docker-compose.yml up -d db` / `up --build migrations` |

Полный перечень скриптов, их параметры, конфигурация `.env` и регламент запуска — только в `docs/13-operations.md` (единственный источник; сюда не копировать).

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
- `docs/` — документация (версионируется; актуальная общая версия — в `docs/README.md`)
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
4. После завершения и проверки стабильной значимой группы изменений подготовить рекомендуемый аннотированный git-тег с понятной разницей с предыдущим тегом: добавленные возможности, исправления, существенные изменения и пройденные проверки. Для больших выпусков подготовить развёрнутые release notes. Создавать или публиковать тег только по явному запросу пользователя после его ручной проверки; при публикации нового тега в GitHub одновременно создать GitHub Release с тем же тегом и развёрнутыми release notes (`docs/16-working-process.md` §3.1).
5. Секреты не коммитить.
6. Перед реализацией задачи из дорожной карты проверить достаточность документации по ней (обоснование, FR/UC, модель данных, API, UI); при нехватке — зафиксировать дизайн в `docs/` и показать пользователю на подтверждение; реализация начинается только после согласования (правки вносятся по замечаниям).
7. После завершения задачи — задокументировать, как пользоваться результатом: что запускать, где появился функционал, команды/API (13-operations, 14-web-interface, 10-api-specification, 17-quickstart) и обновить статус в 12-roadmap.
8. Новые скрипты в `scripts/` обязательно регистрировать в админ-панели веб-интерфейса (`app/admin/runner.py`, список `SCRIPTS`) с описанием назначения и периодичности запуска.

## Notes

- Документация: актуальные версия комплекта, перечень и назначение документов — в `docs/README.md`; версия каждого документа — в его шапке. По правилу SSOT (`docs/16-working-process.md` §2.1) номера версий и состав комплекта сюда не копируются.
- Статус этапов и список реализованного — в `docs/12-roadmap.md` (единственный источник; сюда не копировать).
- `/indicators` строится из реестра `app/market/indicators/registry.py`: новый индикатор в реестре автоматически получает вкладку.
- E2E-тесты (`tests/e2e/`, Playwright Test Runner, TypeScript) запускаются отдельным прогоном `npx playwright test` (UI-режим `--ui`); в `pytest -q` не входят; расхождения, найденные e2e, фиксируются в реестре док. 21 (§6).
