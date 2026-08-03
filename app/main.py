from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.admin.roles import promote_admin_users
from app.admin.runner import recover_stale_runs
from app.api.router import api_router
from app.config import get_settings
from app.db.connection import SessionLocal, init_db
from app.web.router import router as web_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    if settings.auto_create_schema:
        await init_db()
    stale = await recover_stale_runs()
    if stale:
        print(f"Marked {stale} interrupted script run(s) as failed")
    if settings.admin_username_list:
        async with SessionLocal() as session:
            promoted = await promote_admin_users(session, settings.admin_username_list)
            if promoted:
                print(f"Promoted {promoted} admin user(s)")
    yield


app = FastAPI(
    title="NewsTrader Assistant",
    version="0.15.2",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="app/web/static"), name="static")
app.include_router(web_router)
app.include_router(api_router)
