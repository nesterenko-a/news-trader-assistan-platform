from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.db.connection import init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="NewsTrader Assistant",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(api_router)
