from datetime import datetime

from pydantic import BaseModel


class SecurityOut(BaseModel):
    ticker: str
    name: str
    market: str
    sector: str


class SecuritySummary(BaseModel):
    ticker: str
    name: str
    market: str
    security_type: str
    sector: str
    currency: str


class SignalOut(BaseModel):
    entity: str
    snippet: str
    url: str
    sentiment: str
    kind: str
    path: list[str]
    weight: float


class StrategyOut(BaseModel):
    verdict: str
    horizon: str
    confidence: float
    net_score: float
    levels: dict


class StrategyResponse(BaseModel):
    security: SecurityOut
    strategy: StrategyOut
    signals: list[SignalOut]
    quotes: dict | None
    rationale_summary: str
    strategy_id: int | None = None


class NewsItemOut(BaseModel):
    id: int
    title: str
    url: str
    published_at: datetime
    source_name: str
    summary: str
    entities: list[dict]
