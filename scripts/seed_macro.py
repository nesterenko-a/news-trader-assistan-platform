import asyncio
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select

from app.db.connection import SessionLocal, init_db
from app.db.models import MacroEvent, Security, macro_event_security

MONTHS_AHEAD = 6


def _utc(year: int, month: int, day: int, hour_utc: int = 7) -> datetime:
    return datetime(year, month, day, hour_utc, tzinfo=timezone.utc)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        last = date(year, 12, 31)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    while last.weekday() != weekday:
        last -= timedelta(days=1)
    return last


def _build_events(today: date) -> list[dict]:
    events: list[dict] = []
    y0, m0 = today.year, today.month
    for i in range(MONTHS_AHEAD):
        offset = m0 + i
        yy = y0 + (offset - 1) // 12
        mm = (offset - 1) % 12 + 1

        events.append(
            {
                "event_type": "central_bank_meeting",
                "title": "Заседание Банка России по ключевой ставке",
                "event_time": _utc(yy, mm, 15, 7),
                "region": "RU",
                "expected_impact": "high",
                "market_wide": True,
                "description": "Решение по ключевой ставке и сигнал о дальнейшей денежно-кредитной политике",
            }
        )
        fomc = _last_weekday(yy, mm, 2)
        events.append(
            {
                "event_type": "central_bank_meeting",
                "title": "Заседание ФРС (FOMC)",
                "event_time": _utc(fomc.year, fomc.month, fomc.day, 18),
                "region": "US",
                "expected_impact": "high",
                "market_wide": True,
                "description": "Решение по ставке ФРС и прогноз; влияет на глобальный аппетит к риску",
            }
        )
        events.append(
            {
                "event_type": "cpi",
                "title": "Публикация индекса потребительских цен РФ",
                "event_time": _utc(yy, mm, 8, 7),
                "region": "RU",
                "expected_impact": "medium",
                "market_wide": True,
                "description": "Данные по инфляции — влияет на ожидания по ключевой ставке",
            }
        )
        events.append(
            {
                "event_type": "cpi",
                "title": "Публикация CPI США",
                "event_time": _utc(yy, mm, 10, 12),
                "region": "US",
                "expected_impact": "medium",
                "market_wide": True,
                "description": "Американская инфляция — контекст для глобальных рынков",
            }
        )
        events.append(
            {
                "event_type": "pmi",
                "title": "Публикация PMI США (ISM)",
                "event_time": _utc(yy, mm, 1, 14),
                "region": "US",
                "expected_impact": "medium",
                "market_wide": True,
                "description": "Индекс деловой активности в промышленности США",
            }
        )

    for quarter in (3, 6, 9, 12):
        if date(y0, quarter, 1) < today:
            continue
        yy = y0 if quarter >= today.month else y0
        events.append(
            {
                "event_type": "gdp",
                "title": "Публикация ВВП РФ за квартал",
                "event_time": _utc(yy, quarter, 20, 7),
                "region": "RU",
                "expected_impact": "medium",
                "market_wide": True,
                "description": "Оценка динамики ВВП России",
            }
        )
        events.append(
            {
                "event_type": "earnings_season",
                "title": "Сезон отчётностей российских эмитентов",
                "event_time": _utc(yy, quarter, 15, 7),
                "region": "RU",
                "expected_impact": "medium",
                "market_wide": True,
                "description": "Период публикации финансовой отчётности",
            }
        )

    return events


_COMPANY_EVENTS = [
    ("central_bank_meeting", "Годовое собрание акционеров Сбербанка", "medium", "SBER"),
    ("earnings_season", "Отчётность Сбербанка по МСФО", "high", "SBER"),
    ("earnings_season", "Отчётность Газпрома по МСФО", "high", "GAZP"),
    ("earnings_season", "Отчётность Аэрофлота по МСФО", "medium", "AFLT"),
    ("earnings_season", "Отчётность Лукойла по МСФО", "medium", "LKOH"),
    ("earnings_season", "Отчётность НЛМК по МСФО", "medium", "NLMK"),
    ("earnings_season", "Отчётность Ozon по МСФО", "high", "OZON"),
]


def _build_company_events(today: date) -> list[dict]:
    events: list[dict] = []
    for event_type, title, impact, ticker in _COMPANY_EVENTS:
        event_time = _utc(today.year, today.month, 15, 7) + timedelta(days=30)
        events.append(
            {
                "event_type": event_type,
                "title": title,
                "event_time": event_time,
                "region": "RU",
                "expected_impact": impact,
                "market_wide": False,
                "description": "",
                "tickers": [ticker],
            }
        )
    return events


async def main() -> None:
    await init_db()
    async with SessionLocal() as session:
        count = await session.scalar(select(func.count()).select_from(MacroEvent))
        if count:
            print(f"Macro events already seeded ({count}); skipping")
            return

        securities = {
            s.ticker: s.id for s in (await session.scalars(select(Security))).all()
        }
        today = date.today()
        events = _build_events(today) + _build_company_events(today)
        created = 0
        for item in events:
            tickers = item.pop("tickers", [])
            event = MacroEvent(**item)
            session.add(event)
            await session.flush()
            for ticker in tickers:
                security_id = securities.get(ticker)
                if security_id is None:
                    continue
                await session.execute(
                    macro_event_security.insert().values(
                        event_id=event.id, security_id=security_id
                    )
                )
            created += 1
        await session.commit()
        print(f"Macro events seeded: {created}")


if __name__ == "__main__":
    asyncio.run(main())
