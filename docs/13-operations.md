# 13. Инфраструктура и эксплуатация

**Статус:** утверждено v1.16  
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
1. Сбор и анализ новостей (RSS + LLM; при `--telegram` — дополнительно из Telegram-каналов).
2. Синхронизация дневных свечей по всем бумагам (MOEX ISS).
3. Генерация и сохранение стратегий по всем бумагам (накапливается история вердиктов для бэктеста).

**Telegram-источник:** чтение каналов через Telethon (Telegram API). Требуется `TELEGRAM_API_ID`/`TELEGRAM_API_HASH`/`TELEGRAM_CHANNELS` в `.env` и одноразовый вход: `python -m scripts.telegram_login` (запрашивает номер телефона и код). После этого сообщения из указанных каналов попадают в конвейер как источник `telegram` (репутация 0.6), дедупликация по ссылке `t.me/<канал>/<id>`. Включение в сборе: `--telegram`.

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
| `scripts/collect_news.py` | Собирает RSS, фильтрует релевантное, анализирует через LLM, сохраняет в БД (шаг конвейера). Окно сбора: `--days N` / `--from YYYY-MM-DD`; точечный сбор по сущностям: `--entity <имя>` (повторяемый); Telegram-каналы: `--telegram` |
| `scripts/update_prices.py` | Синхронизирует дневные свечи из MOEX ISS; `--days N` — обновление за N дней; `--from YYYY-MM-DD` — полный бэкфилл истории |
| `scripts/daily_pipeline.py` | Ежедневный конвейер: новости → цены → генерация стратегий. Окно сбора новостей: `--days N` / `--from YYYY-MM-DD` |
| `scripts/run_app.py` | Единый запуск: веб-интерфейс (uvicorn) + Telegram-бот |
| `scripts/run_bot.py` | Запуск только Telegram-бота (polling) |
| `scripts/calibrate.py` | Анализирует распределение скоринга по всем бумагам без сохранения стратегий (для настройки порогов) |
| `scripts/backtest.py` | Оценивает сохранённые вердикты против фактического движения цены по дневным свечам (вход = close на дату генерации, выход = close через 5 торговых дней) |
| `scripts/backtest_asof.py` | Бэктест «на момент T»: воспроизводит вердикт на историческую дату, используя только данные, доступные в T (новости до T, без рыночных котировок), и оценивает результат через N торговых дней. Параметры: `--tickers` (по умолчанию все), `--start`/`--end YYYY-MM-DD`, `--horizon N` (торговых дней, по умолчанию 5), `--step N` (шаг по торговым дням, по умолчанию 1). Отчёт: точность по вердиктам и по месяцам, средняя доходность |
| `scripts/smoke.py` | Сквозная проверка API и веб-интерфейса на SQLite |
| `scripts/create_user.py` | Создание пользователя: `python -m scripts.create_user <username> <password>` (аналог регистрации через веб) |
| `scripts/process_alerts.py` | Генерирует алерты по значимым новостям из watchlist всех пользователей; `--days N` — окно новостей (по умолчанию 7) |
| `scripts/seed_macro.py` | Наполняет макрокалендарь на 6 месяцев вперёд: заседания ЦБ, CPI/PMI/ВВП, сезоны отчётностей и корпоративные события по тикерам (идемпотентен) |
| `scripts/telegram_login.py` | Первый вход Telethon для чтения Telegram-каналов (интерактивный: телефон + код), создаёт файл сессии |

Запуск: `.venv\Scripts\python.exe -m scripts.<имя>` из корня проекта.

**Запуск скриптов из веб-интерфейса:** администраторы (роль `admin`, см. [11-security-compliance.md](./11-security-compliance.md)) могут запускать основные скрипты через страницу `/admin` — с историей запусков и выводом (журнал в таблице `script_runs`).

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
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | Данные Telegram API для чтения каналов как источника новостей (Telethon) |
| `TELEGRAM_CHANNELS` | Список каналов-источников через запятую (без `@`) |
| `TELEGRAM_SESSION_NAME` | Имя файла сессии Telethon (по умолчанию `telethon_session`) |
| `APP_URL` | Публичный адрес веб-интерфейса (ссылки в ответах бота) |
| `MVP_TICKERS` | Список отслеживаемых тикеров |

Секреты (ключи API, токены) хранятся только в `.env` и не попадают в репозиторий.

### 6.1. Получение `TELEGRAM_API_ID` / `TELEGRAM_API_HASH`

`api_id` и `api_hash` — это ключи **приложения Telegram API** (не токен бота). Получаются бесплатно на официальном сайте [my.telegram.org](https://my.telegram.org/):

1. Откройте https://my.telegram.org/ и войдите под номером телефона, который используется в Telegram (придёт код подтверждения в мессенджер).
2. Перейдите в раздел **API development tools**.
3. Если приложение ещё не создано — нажмите **Create new application**:
   - **App title** — любое название, например `NewsTraderBot`;
   - **Short name** — короткое имя без пробелов, например `newstrader`;
   - **URL** — можно оставить пустым;
   - **Platform** — Desktop;
   - **Description** — любое описание (можно оставить пустым).
4. После создания на странице отобразятся **api_id** (число) и **api_hash** (строка). Пропишите их в `.env`:

   ```
   TELEGRAM_API_ID=12345678
   TELEGRAM_API_HASH=0123456789abcdef0123456789abcdef
   ```

5. **Важно:** `api_id`/`api_hash` привязаны к аккаунту и считаются секретами — не коммитьте их в git и не публикуйте. Один аккаунт может создать ограниченное число приложений; уточняйте лимит на [my.telegram.org](https://my.telegram.org/).
