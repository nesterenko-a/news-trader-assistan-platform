import asyncio
import os

from app.db.connection import SessionLocal, init_db
from app.db.models import RealtimeConfig
from app.graph.service import seed_graph


async def main() -> None:
    await init_db()
    async with SessionLocal() as session:
        print("Наполнение справочников: бумаги, сущности, связи графа...", flush=True)
        await seed_graph(session)
        # Singleton-настройка realtime (docs/24): создаётся при сидинге, если её нет.
        if await session.get(RealtimeConfig, 1) is None:
            session.add(RealtimeConfig(id=1))
            await session.commit()
            print("Настройка realtime создана", flush=True)
    print("Справочники наполнены", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
    # Принудительный выход: обычный shutdown блокируется финализаторами SQLite,
    # когда скрипт запущен из админки параллельно с работающим приложением на
    # том же файле БД (см. E2E-R3 в docs/21-web-e2e-tests.md). Вся работа уже
    # закоммичена, на PostgreSQL не воспроизводится.
    os._exit(0)
