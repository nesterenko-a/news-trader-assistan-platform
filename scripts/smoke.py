import asyncio
import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./smoke.db"

from fastapi.testclient import TestClient

from app.db.connection import SessionLocal, engine, init_db
from app.graph.service import seed_graph
from app.main import app


async def _seed() -> None:
    await init_db()
    async with SessionLocal() as session:
        await seed_graph(session)


asyncio.run(_seed())

try:
    with TestClient(app) as client:
        health = client.get("/v1/health")
        print("health:", health.status_code, health.json())

        securities = client.get("/v1/securities")
        print("securities:", securities.status_code, [s["ticker"] for s in securities.json()])

        search = client.get("/v1/securities/search", params={"q": "аэро"})
        print("search 'аэро':", search.status_code, [s["ticker"] for s in search.json()])

        strategy = client.post("/v1/securities/AFLT/strategy")
        body = strategy.json()
        print("strategy AFLT:", strategy.status_code, body["strategy"]["verdict"])

        unknown = client.post("/v1/securities/XXXX/strategy")
        print("strategy unknown:", unknown.status_code)
finally:
    asyncio.run(engine.dispose())
