from fastapi import APIRouter

from app.api.routes import (
    alerts,
    auth,
    health,
    macro,
    paper,
    portfolio,
    securities,
    strategies,
    strategy,
    watchlist,
)

api_router = APIRouter(prefix="/v1")
api_router.include_router(health.router)
api_router.include_router(securities.router)
api_router.include_router(strategy.router)
api_router.include_router(auth.router)
api_router.include_router(watchlist.router)
api_router.include_router(portfolio.router)
api_router.include_router(strategies.router)
api_router.include_router(alerts.router)
api_router.include_router(macro.router)
api_router.include_router(paper.router)
