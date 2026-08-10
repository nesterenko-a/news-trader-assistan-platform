from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import UniqueConstraint

from app.db.connection import Base

security_entity = Table(
    "security_entity",
    Base.metadata,
    Column("security_id", ForeignKey("securities.id"), primary_key=True),
    Column("entity_id", ForeignKey("entities.id"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(20), default="user")
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ScriptRun(Base):
    __tablename__ = "script_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    script_name: Mapped[str] = mapped_column(String(50))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running")
    exit_code: Mapped[int | None] = mapped_column(nullable=True)
    output: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class TelegramLinkCode(Base):
    __tablename__ = "telegram_link_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class WatchlistItem(Base):
    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PortfolioPosition(Base):
    __tablename__ = "portfolio_positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), index=True)
    quantity: Mapped[float] = mapped_column(Float)
    avg_price: Mapped[float] = mapped_column(Float)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), index=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"))
    headline: Mapped[str] = mapped_column(String(500), default="")
    url: Mapped[str] = mapped_column(String(1000), default="")
    impact: Mapped[float] = mapped_column(Float, default=0.0)
    is_ambiguous: Mapped[bool] = mapped_column(Boolean, default=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AlertSettings(Base):
    __tablename__ = "alert_settings"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    min_impact: Mapped[float] = mapped_column(Float, default=0.7)
    channels: Mapped[list] = mapped_column(JSON, default=list)


macro_event_security = Table(
    "macro_event_securities",
    Base.metadata,
    Column("event_id", ForeignKey("macro_events.id"), primary_key=True),
    Column("security_id", ForeignKey("securities.id"), primary_key=True),
)


class MacroEvent(Base):
    __tablename__ = "macro_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(300))
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    region: Mapped[str] = mapped_column(String(20), default="RU")
    expected_impact: Mapped[str] = mapped_column(String(10), default="medium")
    market_wide: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str] = mapped_column(Text, default="")


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(50))
    reputation_score: Mapped[float] = mapped_column(Float, default=0.5)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    category: Mapped[str] = mapped_column(String(50), default="")
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_status: Mapped[str | None] = mapped_column(String(10), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    use_llm: Mapped[bool] = mapped_column(Boolean, default=False)
    use_browser: Mapped[bool] = mapped_column(Boolean, default=False)


class UserSource(Base):
    __tablename__ = "user_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    __table_args__ = (UniqueConstraint("user_id", "source_id"),)


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    text: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(String(1000), unique=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    source_reputation: Mapped[float] = mapped_column(Float, default=0.5)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    language: Mapped[str] = mapped_column(String(10), default="ru")
    cluster_id: Mapped[int | None] = mapped_column(ForeignKey("articles.id"), nullable=True)
    analysis_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Entity(Base):
    __tablename__ = "entities"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(50), index=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class Influence(Base):
    __tablename__ = "influences"

    id: Mapped[int] = mapped_column(primary_key=True)
    from_entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)
    to_entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)
    direction: Mapped[str] = mapped_column(String(10))
    strength: Mapped[str] = mapped_column(String(10), default="medium")
    kind: Mapped[str] = mapped_column(String(10), default="direct")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    rationale: Mapped[str] = mapped_column(Text, default="")
    source_ref: Mapped[str] = mapped_column(String(1000), default="")
    created_by: Mapped[str] = mapped_column(String(20), default="curator")
    is_approved: Mapped[bool] = mapped_column(Boolean, default=True)


class ArticleEntity(Base):
    __tablename__ = "article_entities"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), index=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)
    sentiment: Mapped[str] = mapped_column(String(10), default="neutral")
    topic: Mapped[str] = mapped_column(String(50), default="")
    impact: Mapped[float] = mapped_column(Float, default=0.0)
    snippet: Mapped[str] = mapped_column(Text, default="")
    entity_role: Mapped[str] = mapped_column(String(10), default="secondary")


class Security(Base):
    __tablename__ = "securities"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(300))
    market: Mapped[str] = mapped_column(String(20), default="MOEX")
    security_type: Mapped[str] = mapped_column(String(20), default="stock")
    sector: Mapped[str] = mapped_column(String(100), default="")
    currency: Mapped[str] = mapped_column(String(10), default="RUB")
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    assetcode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    lastdeldate: Mapped[date | None] = mapped_column(nullable=True)


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(primary_key=True)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(nullable=True)
    verdict: Mapped[str] = mapped_column(String(20))
    horizon: Mapped[str] = mapped_column(String(20), default="medium")
    confidence: Mapped[str] = mapped_column(String(20), default="low")
    entry_price: Mapped[float | None] = mapped_column(nullable=True)
    take_profit: Mapped[float | None] = mapped_column(nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(nullable=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    model_version: Mapped[str] = mapped_column(String(50), default="mvp-0.1")
    rationale_summary: Mapped[str] = mapped_column(Text, default="")


class UserFeedback(Base):
    __tablename__ = "user_feedback"
    __table_args__ = (
        UniqueConstraint("strategy_id", "user_id", name="uq_user_feedback_strategy_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    rating: Mapped[str] = mapped_column(String(20))
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EvidenceItem(Base):
    __tablename__ = "evidence_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    article_id: Mapped[int | None] = mapped_column(ForeignKey("articles.id"), nullable=True)
    graph_path: Mapped[list] = mapped_column(JSON, default=list)
    indicator_ref: Mapped[dict] = mapped_column(JSON, default=dict)
    quote: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(1000), default="")
    weight: Mapped[float] = mapped_column(Float, default=0.0)


class MarketCandle(Base):
    __tablename__ = "market_candles"

    id: Mapped[int] = mapped_column(primary_key=True)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), index=True)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    open: Mapped[float | None] = mapped_column(nullable=True)
    high: Mapped[float | None] = mapped_column(nullable=True)
    low: Mapped[float | None] = mapped_column(nullable=True)
    close: Mapped[float | None] = mapped_column(nullable=True)
    volume: Mapped[int] = mapped_column(BigInteger, default=0)


class MarketOpenPosition(Base):
    __tablename__ = "market_open_positions"
    __table_args__ = (
        UniqueConstraint(
            "security_id", "trading_date", name="ix_market_open_positions_security_date"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), index=True)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    open_position: Mapped[int] = mapped_column(BigInteger, default=0)
    open_position_value: Mapped[float | None] = mapped_column(nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="iss")


class MarketOpenPositionClientGroup(Base):
    """Открытые позиции фьючерса по группам клиентов (физ/юр лица)."""

    __tablename__ = "market_open_positions_client_groups"
    __table_args__ = (
        UniqueConstraint(
            "security_id", "trading_date", "client_group",
            name="ix_mopcg_security_date_group",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"), index=True)
    trading_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    client_group: Mapped[str] = mapped_column(String(20), nullable=False)  # physical | juridical
    long_pos: Mapped[int] = mapped_column(BigInteger, default=0)
    short_pos: Mapped[int] = mapped_column(BigInteger, default=0)
    net_pos: Mapped[int] = mapped_column(BigInteger, default=0)
    participants: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[int] = mapped_column(BigInteger, default=0)


class PaperAccount(Base):
    __tablename__ = "paper_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    initial_capital: Mapped[float] = mapped_column(Float, default=1_000_000.0)
    currency: Mapped[str] = mapped_column(String(10), default="RUB")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PaperPosition(Base):
    __tablename__ = "paper_positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id"), index=True)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"))
    quantity: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    entry_strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id"), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    status: Mapped[str] = mapped_column(String(10), default="open")
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    exit_price: Mapped[float | None] = mapped_column(nullable=True)
    realized_pnl: Mapped[float | None] = mapped_column(nullable=True)


class PaperTrade(Base):
    __tablename__ = "paper_trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id"), index=True)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id"))
    side: Mapped[str] = mapped_column(String(10))
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id"), nullable=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FactorWeight(Base):
    __tablename__ = "factor_weights"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(50), unique=True)
    factors: Mapped[dict] = mapped_column(JSON)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SystemNotice(Base):
    __tablename__ = "system_notices"

    id: Mapped[int] = mapped_column(primary_key=True)
    level: Mapped[str] = mapped_column(String(10))  # warning / critical
    text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(50), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FuturesTemplate(Base):
    """Сохранённый шаблон списка фьючерсов (SECID) для отслеживания OI."""

    __tablename__ = "futures_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    tickers: Mapped[str] = mapped_column(Text, nullable=False)  # CSV SECID
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
