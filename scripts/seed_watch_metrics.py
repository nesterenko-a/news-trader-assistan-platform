import asyncio

from sqlalchemy import select

from app.db.connection import SessionLocal, init_db
from app.db.models import Entity, EntityWatchMetric
from app.graph.service import resolve_entity_id

# Кураторское наполнение метрик «что отслеживать» для ключевых сущностей.
# (entity_name, label, metric) — label = короткое имя, metric = пояснение.
WATCH_METRICS = [
    ("Нефть", "Brent", "Цена марки Brent"),
    ("Нефть", "WTI", "Цена марки WTI"),
    ("Нефть", "спред крекинга", "Crack spread (нефть → топливо)"),
    ("Природный газ", "TTF", "Цена газа TTF (европейский хаб)"),
    ("Природный газ", "Henry Hub", "Цена газа Henry Hub (США)"),
    ("Золото", "золото", "Цена золота, $/oz"),
    ("Уголь", "коксующийся уголь", "Цена коксующегося угля"),
    ("Доллар США", "USD/RUB", "Курс доллара к рублю"),
    ("Евро", "EUR/RUB", "Курс евро к рублю"),
    ("Ключевая ставка ЦБ", "ставка ЦБ", "Ключевая ставка Банка России"),
    ("Инфляция", "ИПЦ", "Индекс потребительских цен, г/г"),
    ("Потребительский спрос", "розница", "Динамика розничного товарооборота"),
    ("Авиаперевозки", "топливо", "Расходы авиакомпаний на топливо"),
    ("Авиаперевозки", "пассажиропоток", "Пассажирооборот / RPK"),
    ("Авиаперевозки", "загрузка", "Коэффициент занятости кресел (load factor)"),
    ("Нефтегазовый сектор", "цены нефти/газа", "Цены на энергоносители"),
    ("Банковский сектор", "ставки", "Процентные ставки и маржа"),
    ("Химическая промышленность", "удобрения", "Спрос/цены на минеральные удобрения"),
    ("Металлургия", "сталь", "Цены на сталь и сырьё (руда, уголь)"),
    ("Цветная металлургия", "металлы", "Цены на алюминий/медь/никель/палладий"),
    ("Добыча драгметаллов", "золото", "Цена золота"),
    ("Электроэнергетика", "выработка", "Электропотребление и тарифы"),
    ("Ритейл", "потребительский спрос", "Реальные доходы и потребление"),
    ("Транспорт", "грузооборот", "Грузооборот и ставки перевозки"),
]


async def main() -> None:
    await init_db()
    async with SessionLocal() as session:
        created = skipped = 0
        for entity_name, label, metric in WATCH_METRICS:
            entity_id = await resolve_entity_id(session, entity_name)
            if entity_id is None:
                print(f"  ПРОПУСК: сущность не найдена: {entity_name}", flush=True)
                skipped += 1
                continue
            exists = await session.scalar(
                select(EntityWatchMetric).where(
                    EntityWatchMetric.entity_id == entity_id,
                    EntityWatchMetric.label == label,
                    EntityWatchMetric.metric == metric,
                )
            )
            if exists is not None:
                print(f"  ДУБЛЬ: {entity_name} → {label}", flush=True)
                skipped += 1
                continue
            session.add(
                EntityWatchMetric(
                    entity_id=entity_id,
                    label=label,
                    metric=metric,
                    sort_order=0,
                )
            )
            created += 1
            print(f"  Создано: {entity_name} → {label}", flush=True)
        await session.commit()
        print(f"Итого: создано {created}, пропущено {skipped}")


if __name__ == "__main__":
    asyncio.run(main())
