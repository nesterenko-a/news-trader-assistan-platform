# 10. Спецификация API

**Статус:** утверждено v1.5  
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
    }
  ],
  "counterarguments": [
    {
      "kind": "news_fact",
      "quote": "Авиакомпания застраховала топливо на квартал",
      "url": "https://example.com/hedge",
      "weight": -0.15
    }
  ],
  "risks": [
    { "category": "regulatory", "text": "Возможное изменение экспортных пошлин" }
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
- `GET /auth/me` — `{ "id", "username" }` (требует авторизации).

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
- `POST /strategies/{strategy_id}/feedback` — `{ "rating": "worked", "comment": "..." }` — оценка «сработало / частично / не сработало» (rating: worked / partial / failed; повторный вызов обновляет оценку; 400 — недопустимый rating, 404 — стратегия не найдена).
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

### 3.6. Администрирование

- `GET /admin/sources` — список источников и скоринг.
- `POST /admin/sources` — добавить источник.
- `PUT /admin/sources/{source_id}` — изменить (вкл/выкл, скоринг).
- `GET /admin/statistics/ingestion` — статистика сбора.
- `GET /admin/statistics/quality` — метрики качества вердиктов.

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
