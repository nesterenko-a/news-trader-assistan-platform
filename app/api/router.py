from fastapi import APIRouter

from app.api.routes import auth, health, portfolio, securities, strategies, strategy, watchlist

api_router = APIRouter(prefix="/v1")
api_router.include_router(health.router)
api_router.include_router(securities.router)
api_router.include_router(strategy.router)
api_router.include_router(auth.router)
api_router.include_router(watchlist.router)
api_router.include_router(portfolio.router)
api_router.include_router(strategies.router)
