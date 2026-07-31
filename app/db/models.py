from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.connection import Base

security_entity = Table(
    "security_entity",
    Base.metadata,
    Column("security_id", ForeignKey("securities.id"), primary_key=True),
    Column("entity_id", ForeignKey("entities.id"), primary_key=True),
)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(50))
    reputation_score: Mapped[float] = mapped_column(Float, default=0.5)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)


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
