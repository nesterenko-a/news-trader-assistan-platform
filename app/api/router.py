from fastapi import APIRouter

from app.api.routes import (
    alerts,
    auth,
    health,
    indicators,
    macro,
    paper,
    portfolio,
    securities,
    sources,
    strategies,
    strategy,
    tech_analysis,
    top5,
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
api_router.include_router(indicators.router)
api_router.include_router(sources.router)
api_router.include_router(tech_analysis.router)
api_router.include_router(top5.router)
