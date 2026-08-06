# 07. Модель данных

**Статус:** утверждено v1.16  
**Система:** NewsTrader Assistant

Описание сущностей системы и их взаимосвязей. Модель представлена концептуально, без привязки к конкретной СУБД (см. [06-architecture.md](./06-architecture.md)).

## 1. Сводная диаграмма сущностей

```
                    ┌────────────┐          ┌─────────────┐
                    │  User      │ 1      n │ WatchlistItem│
                    │────────────│─────────│─────────────│
                    │ id, role,  │          │ user_id,    │
                    │ settings   │          │ security_id │
                    └────────────┘          └─────────────┘
                          │ 1                      │ n
                          │ n                      │ 1
                    ┌─────▼─────────┐         ┌────▼────────────┐
                    │  PortfolioEntry│         │   Security      │
                    │───────────────│         │─────────────────│
                    │ user_id,      │         │ id, ticker,     │
                    │ security_id,  │────────▶│ name, market,   │
                    │ qty, avg_price│         │ sector          │
                    └───────────────┘         └────┬────────────┘
                                                   │
                     ┌─────────────┐               │
                     │   Article   │               │
                     │─────────────│               │
                     │ id, title,  │──1 n──────────│  (упоминания
                     │ text, url,  │               │   в новостях)
                     │ source_id,  │               │
                     │ published_at│               │
                     └──────┬──────┘               │
                            │ n                    │ n
                            │                      │ 1
                     ┌──────▼────────┐  1 n   ┌────▼────────────┐
                     │ Entity        │────────│   Entity        │
                     │ (сущность)    │<───────│   (сущность)    │
                     │ id, type,     │  n     │                 │
                     │ name, aliases │        └─────────────────┘
                     └──────┬────────┘            ▲
                            │                    │ ребро Influence
                            │ n                  │
                     ┌──────▼─────────┐          │
                     │ ArticleEntity │          │
                     │ (связь статьи │──────────┘
                     │  с сущностью) │
                     │ sentiment,    │
                     │ impact,       │
                     │ topic         │
                     └────────────────┘

                     ┌────────────────┐   n   ┌─────────────────┐
                     │ Quote/TS (ряд) │──────▶│ Security        │
                     │ timestamp,     │       │ (цены/индикаторы)│
                     │ open, high,    │       └─────────────────┘
                     │ low, close, vol│
                     └────────────────┘

                     ┌────────────────┐        ┌─────────────────┐
                     │ MacroEvent     │        │ Strategy        │
                     │ (календарь)    │        │─────────────────│
                     │ type, time,    │        │ security_id,    │
                     │ expected_impact│        │ verdict,        │
                     └────────────────┘        │ horizon,        │
                                              │ confidence,     │
                     ┌────────────────┐        │ generated_at,   │
                     │ EvidenceItem   │        │ model_version   │
                     │ (обоснование)  │        └────────┬────────┘
                     │ strategy_id,   │                 │ n
                     │ article_id,    │                 │ 1
                     │ graph_path,    │        ┌────────▼────────┐
                     │ indicator_ref, │        │ UserFeedback    │
                     │ quote, url     │        │ strategy_id,    │
                     └────────────────┘        │ rating, comment │
                                              └─────────────────┘
```

## 2. Основные сущности

### 2.1. Security (ценная бумага)

| Поле | Тип | Описание |
|---|---|---|
| id | PK | Идентификатор |
| ticker | string | Биржевой код (AFLT, SBER, AAPL) |
| name | string | Полное название |
| market | string | Биржа/рынок (MOEX, NASDAQ и т.д.) |
| security_type | enum | stock / bond / etf / commodity / crypto / futures |
| sector | string | Отрасль (у фьючерсов может быть пустой — наследуется от базовой акции, см. ниже) |
| currency | string | Валюта торгов |
| aliases | string[] | Альтернативные названия для сопоставления с текстами |
| assetcode | string \| null | Код базового актива (для фьючерсов — тикер акции/индекса/валюты, из ISS `ASSETCODE`); по нему строится связь «фьючерс → акция» |
| lastdeldate | date \| null | Дата экспирации (последний день торговли) фьючерса, из ISS `LASTTRADEDATE` |

Связь «акция ↔ фьючерс»: фьючерс связан с акцией, если `assetcode` фьючерса равен `ticker` акции (например, фьючерс SBER-6.26 → assetcode `SBER` → акция SBER). Фьючерсы на индексы/валюты/сырьё (assetcode ≠ тикеру акции) с акциями не связываются. Заполняется при синке OI (`scripts/update_oi.py`, см. [13-operations.md](./13-operations.md)).

### 2.2. Article (новость/статья)

| Поле | Тип | Описание |
|---|---|---|
| id | PK | Идентификатор |
| title | string | Заголовок |
| text | text | Полный текст (нормализованный) |
| url | string | Ссылка на источник |
| source_id | FK | Источник (см. Source) |
| source_reputation | float | Скоринг источника на момент публикации |
| published_at | datetime | Время публикации |
| language | string | Язык |
| cluster_id | FK | Кластер дубликатов (для дедупликации) |
| analysis_version | string | Версия моделей анализа |
| created_at | datetime | Время загрузки в систему |

### 2.3. Source (источник)

| Поле | Тип | Описание |
|---|---|---|
| id | PK | Идентификатор |
| name | string | Название |
| kind | enum | agency / rss / site / telegram / webhook / official |
| reputation_score | float | Оценка достоверности 0..1 |
| is_active | bool | Включён ли сбор |
| config | json | Параметры подключения (для RSS — `{"url": ...}`) |
| category | string | Категория из фиксированного списка |
| last_checked_at | datetime | Время последней проверки |
| last_status | string | `ok` / `error` |
| last_error | string | Текст ошибки проверки |
| use_llm | bool | Включает LLM-разбор ленты при неудаче парсинга |

### 2.4. Entity (сущность) и Influence (связь влияния)

**Entity** — узел knowledge graph:

| Поле | Тип | Описание |
|---|---|---|
| id | PK | Идентификатор |
| type | enum | company / sector / commodity / currency / index / macro_indicator / region / person / event |
| name | string | Основное имя |
| aliases | string[] | Синонимы и сокращения (для извлечения из текста) |
| meta | json | Дополнительно (напр., для macro_indicator — единица измерения) |

**Influence** — ребро графа «A влияет на B»:

| Поле | Тип | Описание |
|---|---|---|
| id | PK | Идентификатор |
| from_entity_id | FK | Сущность-причина |
| to_entity_id | FK | Сущность-следствие |
| direction | enum | positive / negative (знак влияния) |
| strength | enum | weak / medium / strong |
| kind | enum | direct / indirect |
| confidence | float | Уверенность в связи 0..1 |
| rationale | text | Объяснение: почему связь существует (курируется или из источника) |
| source_ref | string | Ссылка на обоснование (статья, аналитический отчёт) |
| created_by | enum | curator / auto_suggested |
| is_approved | bool | Подтверждена ли куратором |

### 2.5. ArticleEntity (упоминание сущности в статье)

| Поле | Тип | Описание |
|---|---|---|
| id | PK | Идентификатор |
| article_id | FK | Статья |
| entity_id | FK | Сущность |
| sentiment | enum | positive / negative / neutral |
| topic | enum | тематика упоминания |
| impact | float | Значимость упоминания для сущности (0..1) |
| snippet | text | Фрагмент текста с упоминанием (для цитаты) |
| entity_role | enum | primary / secondary (главная сущность статьи или фоновая) |

### 2.6. MarketCandle (дневные свечи)

Исторические дневные свечи из MOEX ISS (таблица `market_candles`). Бэкфилл ~1 года + ежедневное обновление.

| Поле | Тип | Описание |
|---|---|---|
| id | PK | Идентификатор |
| security_id | FK | Бумага |
| trading_date | date | Торговая дата |
| open / high / low / close | float | Цены свечи |
| volume | bigint | Объём торгов |

### 2.6.1. MarketOpenPosition (открытые позиции фьючерсов)

Дневные открытые позиции срочного рынка MOEX (таблица `market_open_positions`; источник — ISS `iss/history/engines/futures/markets/forts/securities/{SECID}.json`). Наполняется скриптом `scripts/update_oi.py` (см. [13-operations.md](./13-operations.md)); используется индикатором OI ([19-market-indicators.md](./19-market-indicators.md) §8.12).

| Поле | Тип | Описание |
|---|---|---|
| id | PK | Идентификатор |
| security_id | FK | Фьючерсный контракт (SECID, например W4V6) |
| trading_date | date | Торговая дата |
| open_position | bigint | Открытые позиции, контрактов |
| open_position_value | float | Открытые позиции, руб. (опционально) |
| source | varchar(20) | Источник (iss) |

Уникальность: пара (security_id, trading_date).

### 2.7. Indicator

| Поле | Тип | Описание |
|---|---|---|
| security_id | FK | Бумага |
| timestamp | datetime | Время расчёта |
| type | enum | rsi / macd / ma_20 / ma_50 / volatility / support / resistance |
| value | float | Значение |
| params | json | Параметры индикатора |

### 2.8. MacroEvent (макрокалендарь)

| Поле | Тип | Описание |
|---|---|---|
| id | PK | Идентификатор |
| type | enum | central_bank_meeting / cpi / pmi / gdp / employment / earnings_season / other |
| event_time | datetime | Время события |
| region | string | Регион (US, RU, EU, ...) |
| expected_impact | enum | low / medium / high |
| description | string | Краткое описание |
| affected_entities | FK[] | Связь с сущностями графа |

### 2.9. Strategy (выданная стратегия)

| Поле | Тип | Описание |
|---|---|---|
| id | PK | Идентификатор |
| security_id | FK | Бумага |
| user_id | FK | Пользователь (nullable — общая аналитика) |
| verdict | enum | BUY / SELL / HOLD / INSUFFICIENT_DATA |
| horizon | enum | short / medium / long |
| confidence | enum | low / medium / high |
| entry_price / tp / sl | float | Справочные уровни |
| generated_at | datetime | Время генерации |
| model_version | string | Версия моделей и весов |
| rationale_summary | text | Краткая выжимка обоснования |

### 2.10. EvidenceItem (обоснование)

| Поле | Тип | Описание |
|---|---|---|
| id | PK | Идентификатор |
| strategy_id | FK | Стратегия |
| kind | enum | news_fact / graph_path / indicator / macro_event / research / counterargument |
| article_id | FK | Ссылка на статью (если применимо) |
| graph_path | json | Цепочка влияния: список рёбер |
| indicator_ref | json | Ссылка на индикатор |
| quote | text | Цитата из источника |
| url | string | Ссылка на источник |
| weight | float | Вклад элемента в вердикт |

### 2.11. UserFeedback (обратная связь)

| Поле | Тип | Описание |
|---|---|---|
| id | PK | Идентификатор |
| strategy_id | FK | Стратегия |
| user_id | FK | Пользователь, поставивший оценку |
| rating | enum | worked / partial / neutral / failed («сработало / частично / нейтрально / не сработало») |
| comment | text | Комментарий пользователя |
| created_at | datetime | Время оценки |

Уникально: пара (strategy_id, user_id) — одна оценка на пользователя на стратегию (повторная оценка обновляет запись). Оценки накапливают статистику качества (FR-06-04).

### 2.12. User (пользователь)

| Поле | Тип | Описание |
|---|---|---|
| id | PK | Идентификатор |
| username | string | Уникальное имя пользователя |
| password_hash | string | Хэш пароля (PBKDF2-SHA256) |
| role | enum | user / admin (администраторы — из `ADMIN_USERNAMES`, см. 11-security-compliance.md) |
| telegram_chat_id | int | Привязанный Telegram-чат (после подтверждения кода из бота), null — не привязан |
| created_at | datetime | Время регистрации |

### 2.12.2. ScriptRun (запуск скрипта администратором)

| Поле | Тип | Описание |
|---|---|---|
| id | PK | Идентификатор |
| script_name | string | Ключ скрипта из whitelist (daily_pipeline, collect_news и др.) |
| params | json | Параметры запуска |
| user_id | FK | Администратор, запустивший скрипт |
| status | enum | running / success / failed |
| exit_code | int | Код выхода процесса |
| output | text | Вывод скрипта (stdout+stderr) |
| started_at / finished_at | datetime | Время запуска и завершения |

Записи создаются при запуске скриптов из веб-интерфейса `/admin` (журнал действий администратора).

### 2.12.3. PaperAccount (виртуальный счёт)

| Поле | Тип | Описание |
|---|---|---|
| id | PK | Идентификатор |
| user_id | FK | Пользователь (уникально) |
| initial_capital | float | Стартовый виртуальный капитал |
| currency | string | Валюта (RUB) |
| created_at | datetime | Время создания |

Один виртуальный счёт на пользователя; создаётся автоматически при первом обращении к paper trading (или явно через API).

### 2.12.4. PaperPosition (виртуальная позиция)

| Поле | Тип | Описание |
|---|---|---|
| id | PK | Идентификатор |
| account_id | FK | Виртуальный счёт |
| security_id | FK | Бумага |
| quantity | float | Количество |
| entry_price | float | Цена входа (close на дату сигнала) |
| entry_strategy_id | FK | Вердикт, открывший позицию |
| opened_at | datetime | Время открытия |
| status | enum | open / closed |
| closed_at | datetime | Время закрытия (null пока открыта) |
| exit_price | float | Цена выхода (null пока открыта) |
| realized_pnl | float | Реализованный P&L (для закрытых) |

Позиция закрывается по вердикту SELL или вручную пользователем.

### 2.12.5. PaperTrade (сделка виртуального портфеля)

| Поле | Тип | Описание |
|---|---|---|
| id | PK | Идентификатор |
| account_id | FK | Виртуальный счёт |
| security_id | FK | Бумага |
| side | enum | open / close |
| quantity | float | Количество |
| price | float | Цена сделки |
| strategy_id | FK | Вердикт-сигнал (если сделка по вердикту) |
| ts | datetime | Время сделки |

История сделок привязывается к вердиктам — позволяет сопоставлять результаты paper trading с обратной связью и бэктестом.

### 2.12.6. FactorWeight (версия весов факторов)

| Поле | Тип | Описание |
|---|---|---|
| id | PK | Идентификатор |
| version | string | Номер версии (w1, w2, …), уникален |
| factors | json | Значения факторов: news, graph, counter_penalty |
| description | text | Описание изменений |
| created_at | datetime | Время создания |

Версии весов создаются калибровкой по обратной связи (`scripts/calibrate_weights.py`, см. [09-strategy-engine.md](./09-strategy-engine.md) §6.1). Стратегии ссылаются на версию через `model_version` (`mvp-0.1-w2`) — историчность и A/B-сравнение.

### 2.12.7. SystemNotice (системное уведомление «Внимание»)

| Поле | Тип | Описание |
|---|---|---|
| id | PK | Идентификатор |
| level | enum | info / warning / critical («информационное» / «некритично» / «критично») |
| text | text | Текст сообщения об ошибке в работе программы |
| source | string | Источник (например `script_run` — сбой скрипта) |
| is_active | bool | Активно ли уведомление (показывается ли) |
| created_at | datetime | Время создания |

Уведомления показываются в веб-интерфейсе значком «Внимание» (см. [14-web-interface.md](./14-web-interface.md)). Создаются автоматически при сбое запущенных из админки скриптов и при прерывании запусков перезапуском сервиса.

### 2.12.1. TelegramLinkCode (код привязки Telegram)

| Поле | Тип | Описание |
|---|---|---|
| id | PK | Идентификатор |
| code | string | Уникальный одноразовый код (6 символов) |
| chat_id | int | Telegram-чат, запросивший код |
| created_at | datetime | Время создания |
| expires_at | datetime | Время истечения (15 минут) |

Код создаётся командой бота `/link`, вводится пользователем в веб-интерфейсе (страница алертов) или через API и удаляется после привязки.

### 2.13. Session (сессия авторизации)

| Поле | Тип | Описание |
|---|---|---|
| id | PK | Идентификатор |
| user_id | FK | Пользователь |
| token | string | Уникальный токен (Bearer / cookie `nt_token`) |
| created_at | datetime | Время создания |
| expires_at | datetime | Время истечения (30 дней) |

### 2.14. WatchlistItem (элемент watchlist)

| Поле | Тип | Описание |
|---|---|---|
| id | PK | Идентификатор |
| user_id | FK | Пользователь |
| security_id | FK | Бумага |
| created_at | datetime | Время добавления |

Уникально: пара (user_id, security_id).

### 2.15. PortfolioPosition (позиция портфеля)

| Поле | Тип | Описание |
|---|---|---|
| id | PK | Идентификатор |
| user_id | FK | Пользователь |
| security_id | FK | Бумага |
| quantity | float | Количество |
| avg_price | float | Средняя цена входа |
| opened_at | datetime | Время открытия позиции |

Уникально: пара (user_id, security_id).

### 2.16. Alert (алерт по значимой новости)

| Поле | Тип | Описание |
|---|---|---|
| id | PK | Идентификатор |
| user_id | FK | Пользователь |
| security_id | FK | Бумага |
| article_id | FK | Статья |
| headline | string | Заголовок новости |
| url | string | Ссылка на статью |
| impact | float | Значимость упоминания (0..1) |
| is_ambiguous | bool | Пометка «требует ручной проверки» (нейтральная тональность) |
| is_read | bool | Прочитан ли |
| created_at | datetime | Время создания |

Уникально: пара (user_id, article_id, security_id).

### 2.17. AlertSettings (настройки алертов)

| Поле | Тип | Описание |
|---|---|---|
| user_id | PK/FK | Пользователь |
| min_impact | float | Порог значимости (по умолчанию 0.7) |
| channels | string[] | Каналы доставки: app / telegram |

### 2.18. MacroEvent (событие макрокалендаря)

| Поле | Тип | Описание |
|---|---|---|
| id | PK | Идентификатор |
| event_type | enum | central_bank_meeting / cpi / pmi / gdp / employment / earnings_season / other |
| title | string | Название события |
| event_time | datetime | Время события |
| region | string | Регион (RU / US / EU / global) |
| expected_impact | enum | low / medium / high |
| market_wide | bool | Влияет ли на весь рынок |
| description | text | Краткое описание |

Связь с бумагами — таблица `macro_event_securities` (event_id, security_id): события, затрагивающие конкретных эмитентов.

## 3. Ключевые связи

| Связь | Смысл |
|---|---|
| Article → ArticleEntity → Entity | Статья упоминает сущности с тональностью и значимостью |
| Entity → Influence → Entity | Сущность влияет на другую сущность (знак, сила, обоснование) |
| Security → Entity | Бумага отображается на сущность графа (напр., компания/отрасль) |
| Futures → Security (акция) | Фьючерс связан с базовой акцией по `assetcode == ticker` (для фьючерсов на акции) |
| Strategy → EvidenceItem | Каждый элемент обоснования привязан к стратегии |
| Strategy → UserFeedback | Вердикты оцениваются пользователями |
| User → WatchlistItem → Security | Пользователь отслеживает бумаги |
| User → PortfolioPosition → Security | Позиции пользователя по бумагам |
| User → Alert → Security/Article | Алерты по значимым новостям отслеживаемых бумаг |
| User → Session | Сессии авторизации пользователя |
| PaperAccount → PaperPosition → Security | Виртуальный портфель: позиции по бумагам, привязанные к вердиктам |
| PaperPosition → Strategy | Вердикт, открывший позицию (привязка результатов к вердиктам) |
| MacroEvent → Security | Макрособытие затрагивает бумаги (прямо или рыночно) |
| Security → TimeSeries/Indicator | Рыночные данные по бумаге |
| MacroEvent → Entity | Макрособытие затрагивает сущности |

## 4. Правила целостности

- Связь Influence уникальна для пары (from_entity_id, to_entity_id, created_by).
- ArticleEntity не дублируется для пары (article_id, entity_id).
- MarketCandle уникальна для пары (security_id, trading_date).
- WatchlistItem уникальна для пары (user_id, security_id).
- PortfolioPosition уникальна для пары (user_id, security_id).
- Alert уникальна для тройки (user_id, article_id, security_id).
- MacroEvent ↔ Security не дублируются (составной PK event_id, security_id).
- Session.token уникален.
- Дедупликация: Article с одинаковым cluster_id считается перепечаткой; каноническим считается первый загруженный.
- Изменение модели анализа создаёт новую версию; старые вердикты не перезаписываются (историчность).

## 5. Хранение

| Данные | Рекомендуемый тип хранилища | Причина |
|---|---|---|
| Article, Source, User | документное/реляционное | гибкая схема текстов + целостность пользователей |
| Entity, Influence | графовое | обходы цепочек влияния |
| MarketCandle, Indicator | временные ряды / таблицы | дневные свечи, агрегации, бэктест |
| Strategy, Evidence, Feedback | реляционное | связность и отчёты |
| Частые ответы | кэш | производительность |
