# 13. Инфраструктура и эксплуатация

**Статус:** утверждено v1.10  
**Система:** NewsTrader Assistant

Практическое руководство: как запускается система, как управлять схемой БД, как работает планировщик сбора новостей и какие утилиты доступны.

## 1. Инфраструктура (Docker)

Система разворачивается с помощью Docker Compose. Файл конфигурации — `docker/docker-compose.yml`.

| Сервис | Образ | Назначение |
|---|---|---|
| `db` | postgres:13.3 | Основная БД, порт 5432, том `pgdata` (данные переживают перезапуск), healthcheck |
| `migrations` | liquibase/liquibase:4.31 | Применение миграций схемы; запускается и завершается |

**Запуск БД:**
```
docker compose -f docker/docker-compose.yml up -d db
```

## 2. Миграции (Liquibase)

Схема БД управляется миграциями. Ченджлоги лежат в `liquibase/`:
- `changelog-master.xml` — мастер-ченджлог (список файлов);
- `changelogs/001_initial_schema.xml` — начальная схема (таблицы, ограничения, индексы).

Каждый ченджсет содержит precondition `tableExists/indexExists` с `onFail="MARK_RAN"`, поэтому при применении к существующей базе изменения помечаются выполненными без потери данных.

**Применить миграции:**
```
docker compose -f docker/docker-compose.yml up migrations
```
Применение идемпотентно: повторный запуск ничего не меняет (Run: 0).

**Статус и откат:**
```
docker compose -f docker/docker-compose.yml run --rm migrations status
docker compose -f docker/docker-compose.yml run --rm migrations rollback --count 1
```

**Добавление новой миграции:**
1. Создать `liquibase/changelogs/002_<описание>.xml`.
2. Включить его в `changelog-master.xml` через `<include .../>`.
3. Применить: `docker compose -f docker/docker-compose.yml up migrations`.

Флаг `AUTO_CREATE_SCHEMA` в `.env`: `true` для локальной разработки, `false` в продакшене — приложение не создаёт схему само, ею управляет только Liquibase.

## 3. Планировщик ежедневного конвейера

Сбор и обновление данных выполняются автоматически по расписанию — эквивалент cron на Windows (Task Scheduler).

- Задача: `NewsTraderBot\CollectNews`.
- Расписание: ежедневно в 09:00.
- Скрипт: `scripts/collect_news.bat` (активирует venv и запускает `scripts/daily_pipeline.py`).
- Лог: `logs/daily_pipeline.log`.

**Ежедневный конвейер (`scripts/daily_pipeline.py`) выполняет три шага:**
1. Сбор и анализ новостей (RSS + LLM).
2. Синхронизация дневных свечей по всем бумагам (MOEX ISS).
3. Генерация и сохранение стратегий по всем бумагам (накапливается история вердиктов для бэктеста).

В лог конвейера по каждой бумаге выводится результат генерации стратегии:
- `strategy <TICKER>: STORED verdict=... confidence=... net_score=...` — стратегия записана;
- `strategy <TICKER>: REJECTED (insufficient data)` — бумага отклонена за недостаточностью данных.

В конце фазы выводится сводка: `strategies stored: N (...)` и `strategies rejected (insufficient data): N (...)`.

**Команды планировщика:**
```
schtasks /Query /TN "NewsTraderBot\CollectNews"     # статус
schtasks /Delete /TN "NewsTraderBot\CollectNews" /F # удалить
```

На Linux аналогичная автоматизация выполняется через cron:
```
0 9 * * * cd /path/to/NewsTraderBot && .venv/bin/python -m scripts.daily_pipeline >> logs/daily_pipeline.log 2>&1
```

## 4. Единый запуск приложения и Telegram-бот

**Единый лаунчер `scripts/run_app.py`** поднимает в одном процессе и веб-интерфейс (uvicorn), и Telegram-бота. Запуск: `python -m scripts.run_app` (или `scripts/run_app.bat` — через `pythonw`, без окна консоли).

- Автозапуск: ярлык `NewsTraderBot.lnk` в папке «Автозагрузка» Windows указывает на `scripts/run_app.bat`.
- Лог: `logs/app.log`.
- Если `TELEGRAM_BOT_TOKEN` не задан — запускается только веб-интерфейс (с предупреждением в логе).

**Telegram-бот:**
- Команды: `/start`, `/help`, либо просто тикер (AFLT, SBER, LKOH и т.д.).
- Отдельный запуск только бота: `python -m scripts.run_bot` (полезно для отладки).
- Публичный адрес веб-интерфейса для ссылок в ответах бота — `APP_URL`.

## 5. Утилиты

| Скрипт | Назначение |
|---|---|
| `scripts/seed_db.py` | Создаёт/дополняет справочники: бумаги, сущности, связи графа |
| `scripts/collect_news.py` | Собирает RSS, фильтрует релевантное, анализирует через LLM, сохраняет в БД (шаг конвейера). Окно сбора: `--days N` / `--from YYYY-MM-DD`; точечный сбор по сущностям: `--entity <имя>` (повторяемый) |
| `scripts/update_prices.py` | Синхронизирует дневные свечи из MOEX ISS; `--days N` — обновление за N дней; `--from YYYY-MM-DD` — полный бэкфилл истории |
| `scripts/daily_pipeline.py` | Ежедневный конвейер: новости → цены → генерация стратегий. Окно сбора новостей: `--days N` / `--from YYYY-MM-DD` |
| `scripts/run_app.py` | Единый запуск: веб-интерфейс (uvicorn) + Telegram-бот |
| `scripts/run_bot.py` | Запуск только Telegram-бота (polling) |
| `scripts/calibrate.py` | Анализирует распределение скоринга по всем бумагам без сохранения стратегий (для настройки порогов) |
| `scripts/backtest.py` | Оценивает сохранённые вердикты против фактического движения цены по дневным свечам (вход = close на дату генерации, выход = close через 5 торговых дней) |
| `scripts/smoke.py` | Сквозная проверка API и веб-интерфейса на SQLite |
| `scripts/create_user.py` | Создание пользователя: `python -m scripts.create_user <username> <password>` (аналог регистрации через веб) |

Запуск: `.venv\Scripts\python.exe -m scripts.<имя>` из корня проекта.

**Накопление данных и бэктест:**
- Исторические цены: `python -m scripts.update_prices --from 2012-01-01` (полная история) или `--days N` (обновление).
- Ежедневно конвейер добавляет новости, свечи и вердикты.
- Бэктест становится осмысленным после ~2–4 недель накопления (нужны вердикты, для которых прошло 5 торговых дней).
- Веб-интерфейс показывает историю цены на карточке бумаги: диапазоны 1 год / 5 лет / с 2012 года (серверный SVG-график по сохранённым свечам).

## 6. Конфигурация (`.env`)

| Переменная | Назначение |
|---|---|
| `DATABASE_URL` | Строка подключения к PostgreSQL (asyncpg) |
| `AUTO_CREATE_SCHEMA` | `true` — приложение создаёт схему само (dev); `false` — только Liquibase (prod) |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | Провайдер NLP (DeepSeek, OpenAI-совместимый) |
| `MOEX_BASE_URL` | Базовый URL MOEX ISS |
| `TELEGRAM_BOT_TOKEN` | Токен Telegram-бота (канал доступа) |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | Для чтения Telegram-каналов как источника (этап 1, Telethon) |
| `APP_URL` | Публичный адрес веб-интерфейса (ссылки в ответах бота) |
| `MVP_TICKERS` | Список отслеживаемых тикеров |

Секреты (ключи API, токены) хранятся только в `.env` и не попадают в репозиторий.
