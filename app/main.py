from contextlib import asynccontextmanager
import asyncio
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app.admin.roles import promote_admin_users
from app.admin.runner import recover_stale_runs
from app.api.router import api_router
from app.config import get_settings
from app.db.connection import SessionLocal, init_db
from app.db.models import Source
from app.notices.monitor import run_monitor
from app.web.middleware import DatabaseGuardMiddleware
from app.web.router import router as web_router


async def _check_feeds_background() -> None:
    """Фоновая проверка активных RSS-лент при старте (не блокирует запуск)."""
    from app.news.feed_check import check_feed
    from app.news.sources_service import get_rss_feeds

    try:
        async with SessionLocal() as session:
            feeds = await get_rss_feeds(session)
            for feed in feeds:
                ok, error = await check_feed(feed["url"])
                source = await session.scalar(
                    select(Source).where(Source.name == feed["name"])
                )
                if source is not None:
                    source.last_status = "ok" if ok else "error"
                    source.last_error = "" if ok else error[:500]
                    source.last_checked_at = datetime.now(timezone.utc)
            await session.commit()
            print(f"RSS-ленты проверены: {len(feeds)}", flush=True)
    except Exception as exc:
        print(f"Фоновая проверка RSS не выполнена: {type(exc).__name__}: {exc}", flush=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    try:
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
    except Exception as exc:
        print(f"Startup DB-dependent step failed: {type(exc).__name__}: {exc}")
    monitor_task = asyncio.create_task(run_monitor())
    rss_check_task = asyncio.create_task(_check_feeds_background())
    try:
        yield
    finally:
        monitor_task.cancel()
        rss_check_task.cancel()
        try:
            await monitor_task
            await rss_check_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="NewsTrader Assistant",
    version="0.19.4",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="app/web/static"), name="static")
app.add_middleware(DatabaseGuardMiddleware)
app.include_router(web_router)
app.include_router(api_router)
