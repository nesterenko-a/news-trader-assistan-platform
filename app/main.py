from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.config import get_settings
from app.db.connection import init_db
from app.web.router import router as web_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    if settings.auto_create_schema:
        await init_db()
    yield


app = FastAPI(
    title="NewsTrader Assistant",
    version="0.11.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="app/web/static"), name="static")
app.include_router(web_router)
app.include_router(api_router)
