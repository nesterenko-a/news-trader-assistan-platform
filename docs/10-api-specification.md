# 10. Спецификация API

**Статус:** утверждено v1.29 (добавлено снятие уведомлений «Внимание»: точечное и массовое; ранее — SSE-событие `oi` для фьючерсов)
**Система:** NewsTrader Assistant

Программный интерфейс системы. Спецификация концептуальная; точные схемы запросов/ответов фиксируются на этапе разработки (например, в формате OpenAPI) и должны соответствовать этому документу.

## 1. Общие принципы

- **Базовый URL:** `https://api.newstrader.example/v1`
- **Аутентификация:** API-ключ в заголовке `Authorization: Bearer <key>`.
- **Формат данных:** JSON (все даты — ISO 8601 UTC).
- **Ошибки:** стандартный формат `{ "error": { "code": "...", "message": "...", "details": {...} } }`.
- **Rate limiting:** ограничение частоты запросов; превышение → `429 Too Many Requests` с заголовками `X-RateLimit-*`.
- **Версионирование:** основная версия в URL (`/v1`); обратно совместимые изменения — без смены версии, ломающие — новая версия.

## 2. Коды ошибок

| HTTP | Код | Описание |
|---|---|---|
| 400 | `invalid_request` | Некорректный запрос/параметры |
| 401 | `unauthorized` | Нет или неверный ключ |
| 403 | `forbidden` | Доступ запрещён |
| 404 | `not_found` | Ресурс не найден |
| 409 | `conflict` | Конфликт состояния (дубликат и т.п.) |
| 422 | `insufficient_data` | Недостаточно данных для вердикта |
| 429 | `rate_limited` | Превышен лимит запросов |
| 500 | `internal_error` | Внутренняя ошибка |

## 3. Эндпоинты

### 3.1. Аналитика

#### `POST /securities/{ticker}/strategy`
Запрос стратегии по бумаге.

**Параметры запроса (JSON body):**
| Поле | Тип | Обязательно | Описание |
|---|---|---|---|
| horizon | enum | нет | Ограничение горизонта: short / medium / long |
| lookback_days | integer | нет | Глубина анализа новостей (по умолчанию 7) |
| include_sources | bool | нет | Возвращать ли источники (по умолчанию true) |

**Ответ `200`:**
```json
{
  "security": { "ticker": "AFLT", "name": "Аэрофлот", "market": "MOEX" },
  "strategy": {
    "verdict": "BUY",
    "horizon": "medium",
    "confidence": 0.62,
    "levels": { "entry": 1230.0, "take_profit": 1420.0, "stop_loss": 1140.0 },
    "generated_at": "2026-07-31T10:00:00Z",
    "model_version": "weights-v3.2"
  },
  "evidence": [
    {
      "kind": "news_fact",
      "quote": "Цена нефти выросла на 8% за неделю",
      "url": "https://example.com/oil-news",
      "weight": 0.4
    },
    {
      "kind": "graph_path",
      "path": [
        { "entity": "нефть", "direction": "+", "strength": "strong" },
        { "entity": "нефтегазовый сектор", "direction": "+", "strength": "strong" }
      ],
      "weight": 0.3
    },
    {
      "kind": "indicator",
      "type": "rsi",
      "value": 68.0,
      "weight": -0.1
    },
    {
      "kind": "research",
      "quote": "Цепочка влияния: нефть → нефтегазовый сектор",
      "url": "https://example.com/oil-gaz-research",
      "weight": 0.0
    }
  ],
  "research": [
    "https://example.com/oil-gaz-research"
  ],
  "counterarguments": [
    { "entity": "Авиакомпания", "text": "Авиакомпания: ослабляет (-0.15)", "weight": -0.15 },
    { "entity": "индикаторы", "text": "RSI=72 — зона перекупленности, сигнал ослаблен", "weight": 0.0 }
  ],
  "risks": [
    "отраслевой/корпоративный: Авиакомпания: ослабляет (-0.15)",
    "рыночный: RSI=72 — зона перекупленности, сигнал ослаблен"
  ],
  "disclaimer": "Материал носит информационный характер..."
}
```

**Ответ `422 insufficient_data`** — при нехватке данных для вердикта.

#### `GET /securities/{ticker}/news`
Лента новостей по бумаге.

**Query-параметры:** `from`, `to`, `min_impact`, `sentiment`, `source_id`, `limit` (≤100), `offset`.

**Ответ `200`:** массив статей с метаданными анализа (сущности, тональность, значимость, ссылки).

#### `GET /securities/{ticker}/indicators`
Текущие индикаторы по бумаге.

**Ответ `200`:**
```json
{
  "ticker": "AFLT",
  "as_of": "2026-07-31T10:00:00Z",
  "quotes": { "price": 1230.0, "volume": 1200000, "day_high": 1250.0, "day_low": 1220.0 },
  "indicators": [
    { "type": "rsi", "value": 68.0 },
    { "type": "ma_20", "value": 1185.0 },
    { "type": "ma_50", "value": 1140.0 },
    { "type": "support", "value": 1140.0 },
    { "type": "resistance", "value": 1260.0 }
  ]
}
```

### 3.2. Поиск и справочники

#### `GET /securities/search?q=...`
Поиск бумаг по тикеру или названию. Возвращает кандидатов для уточнения запроса (UC-01, шаг 3b).

#### `GET /securities`
Список поддерживаемых бумаг с фильтрами: `market`, `sector`, `type`, `page`.

#### `GET /macro/calendar`
Макроэкономический календарь. Query: `from`, `to`, `region`, `type`.

#### `GET /graph/entity/{entity_id}`
Карточка сущности графа: связи, сила, обоснование рёбер.

### 3.3. Авторизация и пользовательские данные

Аутентификация — токен сессии в заголовке `Authorization: Bearer <token>` (или cookie `nt_token` для веб-интерфейса). Токен выдаётся при регистрации/входе.

#### Аутентификация
- `POST /auth/register` — `{ "username": "...", "password": "..." }` → `{ "token", "username" }` (201).
- `POST /auth/login` — `{ "username": "...", "password": "..." }` → `{ "token", "username" }`.
- `POST /auth/logout` — завершает сессию (токен из заголовка/cookie).
- `GET /auth/me` — `{ "id", "username", "role" }` (требует авторизации).

#### Watchlist
- `GET /watchlist` — список с последним вердиктом по каждой бумаге.
- `POST /watchlist` — добавить `{ "ticker": "AFLT" }` (201; 409 если уже есть).
- `DELETE /watchlist/{ticker}` — удалить.

#### Портфель
- `GET /portfolio` — позиции с переоценкой по текущим ценам MOEX: `quantity`, `avg_price`, `current_price`, `market_value`, `cost_basis`, `pnl`, `pnl_percent`, `verdict`.
- `POST /portfolio` — добавить `{ "ticker": "AFLT", "quantity": 100, "avg_price": 1100.0 }` (201; 409 если уже есть).
- `PATCH /portfolio/{ticker}` — изменить `{ "quantity"?: ..., "avg_price"?: ... }`.
- `DELETE /portfolio/{ticker}` — закрыть позицию.

#### История стратегий
- `GET /strategies/history?limit=N` — последние сохранённые стратегии (тикер, вердикт, горизонт, уверенность, дата, версия модели, `my_rating` — оценка текущего пользователя).

#### Обратная связь
- `POST /strategies/{strategy_id}/feedback` — `{ "rating": "worked", "comment": "..." }` — оценка «сработало / частично / нейтрально / не сработало» (rating: worked / partial / neutral / failed; повторный вызов обновляет оценку; 400 — недопустимый rating, 404 — стратегия не найдена).
- `GET /strategies/feedback/stats` — статистика оценок пользователя `{ "worked", "partial", "failed", "total", "worked_percent" }`.
- `GET /strategies/{strategy_id}` — детали стратегии с обоснованием (планируется).

### 3.4. Алерты

Алерты формируются из новостей высокой значимости по бумагам из watchlist (см. FR-07). Генерация выполняется в `daily_pipeline` и скриптом `scripts/process_alerts.py`.

- `GET /alerts?unread_only=true` — список алертов пользователя (тикер, заголовок, ссылка, значимость, пометка «требует проверки», прочитан ли).
- `PATCH /alerts/{alert_id}/read` — отметить прочитанным.
- `POST /alerts/read-all` — отметить все прочитанными.
- `GET /alerts/settings` — настройки `{ "min_impact": 0.7, "channels": ["app"] }`.
- `PUT /alerts/settings` — изменить `{ "min_impact"?, "channels"? }` (каналы: app / telegram).
- `POST /alerts/telegram/link` — привязать Telegram-чат по коду из бота `{ "code": "..." }` → `{ "status": "ok", "chat_id" }` (400, если код недействителен или истёк).
- `DELETE /alerts/telegram/link` — отвязать Telegram-чат.

Доставка в интерфейсе — мгновенная (список на странице); при выборе канала `telegram` новые алерты отправляются в привязанный Telegram-чат (команда бота `/link`, см. [15-telegram-bot.md](./15-telegram-bot.md)).

### 3.5. Макрокалендарь

Макроэкономический календарь: заседания ЦБ, CPI/PMI/ВВП, сезоны отчётностей. Наполнение — `scripts/seed_macro.py`.

- `GET /macro/calendar?from=&to=&region=` — список событий с тикерами затронутых бумаг (для рыночных событий `market_wide: true`).
- `GET /macro/securities/{ticker}` — события, затрагивающие конкретную бумагу (рыночные + прямые привязки).

### 3.6. Виртуальный портфель (paper trading)

Имитация торговли по вердиктам на виртуальный капитал (см. UC-07, FR-08-03…08-06). Счёт создаётся автоматически при первом обращении.

- `GET /paper` — счёт и портфель: капитал, открытые позиции с переоценкой и P&L, метрики (доходность, % прибыльных сделок, средний результат, максимальная просадка), сравнение с бенчмарком (IMOEX или «купил всё и держишь»).
- `GET /paper/trades?limit=N` — история виртуальных сделок с привязкой к вердиктам (тикер, сторона, цена, дата, вердикт).
- `DELETE /paper/{ticker}` — вручную закрыть позицию по бумаге.
- `POST /paper/reset` — сбросить портфель: закрыть все позиции, вернуть стартовый капитал.

Позиции открываются/закрываются автоматически в ежедневном конвейере по вердиктам (BUY → покупка, SELL → закрытие) с равными весами и входом по цене закрытия дня сигнала.

### 3.7. Администрирование

- `GET /admin/sources` — список источников и скоринг.
- `POST /admin/sources` — добавить источник.
- `PUT /admin/sources/{source_id}` — изменить (вкл/выкл, скоринг).
- `GET /admin/statistics/ingestion` — статистика сбора.
- `GET /admin/statistics/quality` — метрики качества вердиктов.

### 3.8. Индикаторы (биржа)

- `GET /v1/indicators` — список доступных индикаторов (имя, описание, параметры по умолчанию, сложность).
- `GET /v1/indicators/futures[?q=...]` — все фьючерсы срочного рынка MOEX (поля: `secid`, `shortname`, `secname`, `assetcode`, `lastdeldate`, `prevopenposition`, `lotvolume`, `initialmargin`, `minstep`, `stepprice`, `prevsettleprice`, **`sector`** — отрасль: сектор базового актива из справочника для фьючерсов на акции, иначе категория (Индексы/Валюты/Товары/Проценты/Крипто/Прочее)); `q` — фильтр по коду/названию/базовому активу. Ответ кэшируется на 1 час (источник — ISS `engines/futures/markets/forts/securities.json`).
- `GET /v1/indicators/{name}?ticker=...` — расчёт индикатора. Параметры: `ticker` (обязательный; для OI — код фьючерса SECID, например W4V6), `from`/`to` (диапазон дат), `limit` (ограничение числа свечей), параметры индикатора (для OI: `oi_change_threshold_pct`, `price_change_threshold_pct`; для Volume Profile: `period` — число дней (по умолчанию 60), `bins` — число ценовых баров, `value_area_pct` — % объёма Value Area, `hvn_factor`/`lvn_factor` — пороги узлов высокого/низкого объёма; для EMA: `fast`/`slow` (по умолчанию 12/26); для MACD: `fast`/`slow`/`signal` (по умолчанию 12/26/9); для Поддержка/сопротивление: `window` — окно поиска экстремумов (по умолчанию 20), `fractal_k` (по умолчанию 2), `min_touches` (по умолчанию 2), `cluster_tolerance_atr` (по умолчанию 0.25); для Боллинджера: `period` (по умолчанию 20), `k` — множитель σ (по умолчанию 2); для ATR: `period` (по умолчанию 14); для ADX: `period` (по умолчанию 14)). Для OI дополнительно `client_groups=1` — в `meta.client_groups` возвращаются ряды открытых позиций **по группам клиентов** (физ/юр): по датам `physical`/`juridical` (long/short/net/participants), `summary`, `physical_share_pct` (доля физиков %), `net_spread` (спред нетто «юр − физ») — см. [19-market-indicators.md](./19-market-indicators.md) §8.14.

Пример — OI для фьючерса W4V6:

```json
GET /v1/indicators/oi?ticker=W4V6&from=2026-07-20&to=2026-08-05
{
  "indicator": "oi",
  "params": {"oi_change_threshold_pct": 1.0, "price_change_threshold_pct": 0.0},
  "values": [
    {"date": "2026-07-21", "kind": "oi", "value": 3270.0},
    {"date": "2026-07-21", "kind": "oi_change_pct", "value": 1.0511}
  ],
  "signals": [
    {"date": "2026-07-21", "kind": "strong_bull", "severity": "strong", "note": "Цена растёт, OI растёт — набор длинных позиций"}
  ],
  "meta": {"candles": 12, "from": "2026-07-20", "to": "2026-08-05", "note": "OI доступен только для фьючерсов (срочный рынок MOEX)"}
}
```

Коды ошибок: `404` — неизвестный индикатор; `400` — бумага не найдена или нет данных OI (требуется предварительно выполнить `scripts/update_oi.py --ticker <SECID>`); `422` — невалидные параметры.

Сигналы OI (поле `kind`): `strong_bull` / `strong_bear` (цена и OI движутся в одну сторону — сильные), `bearish_setup` / `bullish_setup` (цена без изменений, OI растёт/падает — подготовка к движению), `long_liquidation` / `short_covering` (цена и OI движутся в разные стороны — закрытие позиций). Трактовки (`note`) содержат стрелки направления: ↑ — рост, ↓ — падение, → — без изменений.

Индикатор **Volume Profile** (`GET /v1/indicators/volume_profile?ticker=AFLT&period=60`): `meta` содержит `nodes` — список узлов профиля (`price`, `volume`, `is_poc`, `in_value_area`, `is_hvn`, `is_lvn`), а также `poc`, `vah`, `val`, `from`/`to`, `candles`; `signals` — `poc`, `value_area`, `hvn`, `lvn` (см. [19-market-indicators.md](./19-market-indicators.md) §8.13).
Индикатор **Поддержка/сопротивление** (`GET /v1/indicators/support_resistance?ticker=AFLT`): `meta` содержит `levels` — список уровней (`price`, `kind`: `support`/`resistance`, `strength`: `medium`/`strong`, `touches` — число касаний), `pivot` (P/R1/R2/S1/S2), `atr`, `last_close`, `from`/`to`, `candles`; `signals` — `breakout_up`/`breakout_down` (пробой уровня) и `bounce_up`/`bounce_down` (отскок от уровня) (см. [19-market-indicators.md](./19-market-indicators.md) §8.11).
Индикатор **Полосы Боллинджера** (`GET /v1/indicators/bollinger?ticker=AFLT&period=20&k=2`): `values` — `middle`/`upper`/`lower`/`percent_b` по датам; `meta` содержит `latest_middle`/`latest_upper`/`latest_lower`, `last_close`, `percent_b`, `zone` (`upper`/`middle`/`lower`), `from`/`to`, `candles`; `signals` — `touch_upper`/`touch_lower`, `revert_in`, `squeeze` (см. [19-market-indicators.md](./19-market-indicators.md) §8.3).
Индикатор **ATR** (`GET /v1/indicators/atr?ticker=AFLT&period=14`): `values` — `atr` по датам (со сглаживанием Уайлдера); `meta` содержит `latest_atr`, `atr_pct` (ATR в % от цены), `last_close`, `from`/`to`, `candles`; сигналов не даёт (см. [19-market-indicators.md](./19-market-indicators.md) §8.8).
Индикатор **ADX/DI** (`GET /v1/indicators/adx?ticker=AFLT&period=14`): `values` — `plus_di`/`minus_di`/`adx` по датам; `meta` содержит `latest_adx`, `latest_plus_di`/`latest_minus_di`, `trend` (`up`/`down`), `state` (`trend`/`range`), `from`/`to`, `candles`; `signals` — `trend` (ADX≥25), `range` (ADX<20), `bullish`/`bearish` (см. [19-market-indicators.md](./19-market-indicators.md) §8.10).

### 3.9. Источники новостей (RSS, сайты компаний)

Управление персональным списком источников новостей пользователя — RSS-лент (`kind: "rss"`) и сайтов компаний (`kind: "website"`, URL страницы-списка новостей в `config.url`) — см. [20-news-sources-manager.md](./20-news-sources-manager.md) и [22-company-sites-source.md](./22-company-sites-source.md). Все эндпоинты требуют авторизации (Bearer или cookie `nt_token`).

- `GET /v1/sources[?kind=rss|website][&category=...]` — список источников пользователя (из `user_sources`): `id`, `name`, `kind`, `url`, `category`, `reputation`, `is_active`, `last_status` (`ok`/`error`), `last_error`, `last_checked_at`, `use_llm`, `use_browser`.
- `POST /v1/sources` — добавить источник в список пользователя (`{name, url, kind: "rss"|"website", category, reputation}`); запись создаётся в каталоге `sources` при необходимости, выполняется проверка работоспособности (для `rss` — `check_feed`, для `website` — `check_website`: HTTP 200 + непустое тело, при `use_llm` — извлечение записей LLM). Ошибки: `400` — невалидный URL/категория/SSRF, `401`.
- `PUT /v1/sources/{id}` — обновить метаданные источника (`{name?, url?, category?, reputation?, is_active?, use_llm?, use_browser?}`).
- `DELETE /v1/sources/{id}` — убрать источник из списка пользователя (из каталога не удаляется).
- `POST /v1/sources/check` — проверить источники (`{ids: [...]}`, пусто = все источники пользователя любого kind): обновляет `last_status`/`last_error`/`last_checked_at`, возвращает обновлённые записи.
- `POST /v1/sources/search` — LLM-поиск новых лент **только для `kind: "rss"`** (`{query, kind: "rss"}`; `kind: "website"` → `400`): DeepSeek генерирует до 8 кандидатов (название, URL, категория), каждый проверяется HTTP-запросом; возвращает `[{name, url, category, ok, error}]` (не добавляет в список).
- `POST /v1/sources/restore-defaults` — вернуть стандартные **ленты** (`DEFAULT_FEEDS`) в список пользователя; ответ `{added: N}`. Стандартные сайты (`DEFAULT_SITES`, пока пусто) восстанавливаются отдельно — веб-роут `POST /news/site/restore`.

### 3.10. Теханализ в LLM (ChatGPT)

Все эндпоинты требуют аутентификации (Bearer-токен или cookie `nt_token`).

- `POST /v1/tech-analysis/start?ticker=...` — запуск теханализа: актуализация данных → формирование запроса → отправка в ChatGPT. Создаёт запись `running`; `409` если активный анализ по тикеру уже идёт; `400/404` при недоступности (нет `CHATGPT_API_KEY`, бумага не найдена).
- `GET /v1/tech-analysis?ticker=...&page=1` — список анализов по тикеру (карточки, ≤4 на страницу): `{items, total, page, per_page, pages}`.
- `GET /v1/tech-analysis/{id}/status` — статус/этап: `{id, ticker, status, stage, request_ready, response, error}`.
- `POST /v1/tech-analysis/{id}/retry` — повтор отправки с тем же `request_md` (для `failed`-записи).
- `GET /v1/tech-analysis/{id}/request.md` — скачать сформированный запрос в Markdown (`text/markdown`, `Content-Disposition: attachment`).
- `GET /v1/tech-analysis/{id}/response.md` — скачать ответ LLM в Markdown.

Веб-страницы:
- `GET /securities/{ticker}` — на карточке акций/фьючерсов кнопка «Теханализ в LLM» + панель этапов + блок карточек результатов (≤4/стр. + пагинация `?ta_page=N`).
- `GET /tech_analysis/{id}` — полный ответ (карточки сценариев A/B/C в шапке из раздела «Моя стратегия», рендер Markdown, кнопки «Скачать запрос/ответ (MD)», «Повторить»).

### 3.11. Top-5: лучшая сделка из шаблона акций (Теханализ группы)

Все эндпоинты требуют аутентификации (Bearer-токен или cookie `nt_token`). Работают с «Шаблонами инструментов» `kind=stock` (см. [25-top5-trades.md](./25-top5-trades.md)).

- `GET /v1/top5?template_id=&limit=` — рейтинг Top-5 лучших сделок по акциям шаблона: `{template, total, as_of, items:[{ticker, name, strategy, dir, entry, stop, targets, rr, probability, expected_r, score, price, as_of, scenarios:{a,b,c}, final_assessment:{key_level, main_risk, recommendation}, analysis_id}]}` (`price` — текущая цена бумаги; `as_of` — дата формирования прогноза; `scenarios` — все 3 сценария A/B/C; `final_assessment` — Ключевой уровень/Главный риск/Моя рекомендация из «Итоговой оценки»).
- `POST /v1/top5/run?template_id=&provider=` — запуск батча Теханализа по акциям шаблона (создаёт `tech_analysis_batches` + по анализу на акцию, переиспользуя актуальные успешные); `409` при активном батче, `404` при неверном шаблоне/отсутствии ключа LLM.
- `GET /v1/top5/{batch_id}/status` — прогресс батча: `{batch_id, status, total, done, success, running, failed, stage}`.
- `GET /v1/top5/{batch_id}` — детали: анализы по акциям шаблона (ticker, status, analysis_id, verdict).

Конфигурация `.env`: `TOP5_FRESH_HOURS` (актуальность анализ для переиспользования, по умолчанию 6), `TOP5_MAX_INSTRUMENTS` (лимит акций в батче, 20).

Веб-страницы:
- `GET /top5` — выбор шаблона (kind=stock), кнопка «Отправить на Теханализ в LLM», таблица Top-5 с раскрытием сценариев A/B/C.
- `GET /admin/futures-templates` — «Шаблоны инструментов» (kind stock/futures; для акций — список из `/v1/indicators/stocks`).

### 3.12. Реальное время (live-котировки, SSE)

Актуальность рыночных данных (акции watchlist+mvp, фьючерсы выбранного шаблона) поддерживает демон `realtime_updater` (см. [13-operations.md](./13-operations.md)); веб-интерфейс транслирует live-котировки из БД по **SSE**. Настройки — в админ-панели, блок «Реальное время».

- `GET /v1/realtime/stream?tickers=AFLT,SBER` — **SSE-стрим** live-котировок. Требует аутентификации (Bearer/cookie `nt_token`). Периодически (каждые ~5 с) читает `RealTimeQuote` по переданным тикерам и шлёт события:

  ```
  event: quote
  data: {"ticker":"AFLT","last":123.0,"open":...,"high":...,"low":...,"volume":...,"ts":"ISO-UTC"}
  ```

  Если по тикеру нет записи — событие не шлётся. Комментарий `: keep-alive` для поддержания соединения; при отключении клиента стрим завершается. Эндпоинт агностичен к демону: читает `RealTimeQuote` из БД.

  Для **фьючерсов** с данными OI дополнительно шлётся `event: oi` (последний `open_position`, изменение к предыдущему дню, группы клиентов физ/юр), читающий `market_open_positions`:

  ```
  event: oi
  data: {"ticker":"W4V6","date":"2026-08-28","open_position":3270,"change_pct":1.05,"groups":{"physical":{"long":...,"short":...,"net":...},"juridical":{...}}}
  ```

- Админ-настройки (роут страницы, только роль `admin`):
  - `POST /admin/realtime/save` — сохранить настройки: `realtime_enabled` (on/off), `interval_quotes_sec`, `interval_candles_sec`, `interval_oi_sec` (положительные), `futures_template_id` (пусто/невалид → шаблон не выбран); 303-редирект на `/admin`.
  - `GET /admin` — контекст страницы содержит `realtime` (+ `realtime_run` — статус последнего запуска демона из `script_runs`).

### 3.13. Системные уведомления «Внимание»

| Метод | Путь | Доступ | Назначение |
|---|---|---|---|
| `GET` | `/api/notices` | все | Список активных уведомлений и состояние значка; для авторизованного пользователя `can_dismiss=true` |
| `POST` | `/api/notices/{id}/dismiss` | авторизованный | Снять одно уведомление с активного списка; ответ `{ "dismissed": 1 }` |
| `POST` | `/api/notices/dismiss-all` | авторизованный | Снять все активные уведомления; ответ `{ "dismissed": N }` |

Снятие меняет `is_active` на `false`, не удаляя диагностическую запись. Если монитор обнаружит новую или сохраняющуюся проблему, он создаст актуальное уведомление повторно.

## 4. Webhook для алертов (исходящие)

Администратор/пользователь может настроить URL, на который система отправляет уведомления:

```
POST {callback_url}
{
  "event": "alert",
  "ticker": "AFLT",
  "impact": 0.82,
  "headline": "…",
  "url": "…",
  "expected_effect": "+"
}
```

## 5. Политика кэширования

- Стратегии кэшируются: повторный запрос по той же бумаге без новых значимых новостей возвращает закэшированный ответ с меткой `cached: true`.
- Принудительный пересчёт — query-параметр `?force=true` (rate-limited).
