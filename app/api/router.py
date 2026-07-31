from fastapi import APIRouter

from app.api.routes import health, securities, strategy

api_router = APIRouter(prefix="/v1")
api_router.include_router(health.router)
api_router.include_router(securities.router)
api_router.include_router(strategy.router)
