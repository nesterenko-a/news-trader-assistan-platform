import asyncio

from app.db.connection import SessionLocal, init_db
from app.graph.service import seed_graph


async def main() -> None:
    await init_db()
    async with SessionLocal() as session:
        await seed_graph(session)
    print("DB seeded: securities, entities, influences")


if __name__ == "__main__":
    asyncio.run(main())
