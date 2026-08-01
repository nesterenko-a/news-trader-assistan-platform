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


class RegisterIn(BaseModel):
    username: str
    password: str


class LoginIn(BaseModel):
    username: str
    password: str


class AuthOut(BaseModel):
    token: str
    username: str


class UserOut(BaseModel):
    id: int
    username: str


class WatchlistItemOut(BaseModel):
    ticker: str
    name: str
    sector: str
    market: str
    verdict: str | None = None
    confidence: str | None = None


class WatchlistAddIn(BaseModel):
    ticker: str


class PositionIn(BaseModel):
    ticker: str
    quantity: float
    avg_price: float


class PositionUpdateIn(BaseModel):
    quantity: float | None = None
    avg_price: float | None = None


class PositionOut(BaseModel):
    ticker: str
    name: str
    sector: str
    quantity: float
    avg_price: float
    current_price: float | None = None
    market_value: float | None = None
    cost_basis: float
    pnl: float | None = None
    pnl_percent: float | None = None
    verdict: str | None = None


class StrategyHistoryItem(BaseModel):
    id: int
    ticker: str
    name: str
    verdict: str
    horizon: str
    confidence: str
    generated_at: datetime
    model_version: str


class AlertOut(BaseModel):
    id: int
    ticker: str
    headline: str
    url: str
    impact: float
    is_ambiguous: bool
    is_read: bool
    created_at: datetime


class AlertSettingsOut(BaseModel):
    min_impact: float
    channels: list[str]


class AlertSettingsIn(BaseModel):
    min_impact: float | None = None
    channels: list[str] | None = None
