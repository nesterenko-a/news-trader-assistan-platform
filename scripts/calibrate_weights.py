import asyncio

from app.db.connection import SessionLocal, init_db
from app.strategy.weights import calibrate


async def main() -> None:
    await init_db()
    async with SessionLocal() as session:
        report = await calibrate(session)
        print("Версия весов:", report["version"])
        print("Факторы:", report["factors"])
        print("Изменения:", report["changes"] or "нет изменений")
        print(
            "Учтеных оценок: worked=%s failed=%s"
            % (report["worked_count"], report["failed_count"])
        )


if __name__ == "__main__":
    asyncio.run(main())
