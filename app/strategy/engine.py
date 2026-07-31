import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Article,
    ArticleEntity,
    EvidenceItem,
    Security,
    Strategy,
)
from app.graph.service import (
    DIRECTION_SIGN,
    STRENGTH_WEIGHT,
    find_influence_paths,
    security_entity_ids,
)
from app.market.indicators import rsi, sma, volatility

DECAY_PER_HOUR = 0.0137
LOOKBACK_DAYS = 7
BUY_THRESHOLD = 0.2
SELL_THRESHOLD = -0.2
MIN_CONFIDENCE_FOR_VERDICT = 0.4
SENTIMENT_SIGN = {"positive": 1.0, "negative": -1.0, "neutral": 0.0}


def _decay_weight(published_at: datetime, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    hours = max((now - published_at).total_seconds() / 3600.0, 0.0)
    return math.exp(-DECAY_PER_HOUR * hours)


def _verdict_for_score(net_score: float) -> str:
    if net_score >= BUY_THRESHOLD:
        return "BUY"
    if net_score <= SELL_THRESHOLD:
        return "SELL"
    return "HOLD"


def _horizon_for_score(net_score: float) -> str:
    if abs(net_score) < 0.5:
        return "short"
    if abs(net_score) < 1.0:
        return "medium"
    return "long"


async def generate_strategy(
    session: AsyncSession, ticker: str, as_of: datetime | None = None
) -> dict:
    now = as_of or datetime.now(timezone.utc)
    security = await session.scalar(select(Security).where(Security.ticker == ticker))
    if security is None:
        raise ValueError(f"Бумага {ticker} не найдена")

    target_entity_ids = await security_entity_ids(session, security.id)
    if not target_entity_ids:
        return _build_result(
            security=security,
            verdict="INSUFFICIENT_DATA",
            confidence=0.0,
            horizon="medium",
            net_score=0.0,
            signals=[],
            quotes=None,
        )

    since = (now - timedelta(days=LOOKBACK_DAYS)).replace(microsecond=0)
    recent_articles = (
        await session.scalars(
            select(Article)
            .where(Article.published_at >= since)
            .order_by(Article.published_at.desc())
        )
    ).all()
    article_ids = [a.id for a in recent_articles]
    if not article_ids:
        return _build_result(
            security=security,
            verdict="INSUFFICIENT_DATA",
            confidence=0.0,
            horizon="medium",
            net_score=0.0,
            signals=[],
            quotes=None,
        )

    article_entities = (
        await session.scalars(
            select(ArticleEntity).where(ArticleEntity.article_id.in_(article_ids))
        )
    ).all()
    articles_by_id = {a.id: a for a in recent_articles}
    entities_by_id = {}
    from app.db.models import Entity
    for e in await session.scalars(select(Entity)):
        entities_by_id[e.id] = e

    signals = []
    path_cache = {}

    for ae in article_entities:
        article = articles_by_id.get(ae.article_id)
        if article is None:
            continue
        entity = entities_by_id.get(ae.entity_id)
        if entity is None:
            continue

        decay = _decay_weight(article.published_at, now)
        base = ae.impact * decay * article.source_reputation

        if ae.entity_id in target_entity_ids:
            contribution = SENTIMENT_SIGN.get(ae.sentiment, 0.0) * base
            signals.append(
                {
                    "entity": entity.name,
                    "snippet": ae.snippet or article.title,
                    "url": article.url,
                    "sentiment": ae.sentiment,
                    "kind": "direct",
                    "path": [entity.name],
                    "weight": contribution,
                }
            )
            continue

        paths = path_cache.get(ae.entity_id)
        if paths is None:
            paths = []
            for target_id in target_entity_ids:
                paths.extend(
                    await find_influence_paths(session, ae.entity_id, target_id)
                )
            path_cache[ae.entity_id] = paths

        if not paths:
            continue
        best = max(paths, key=lambda p: p.strength * p.confidence)
        contribution = (
            SENTIMENT_SIGN.get(ae.sentiment, 0.0)
            * base
            * best.sign
            * best.strength
            * best.confidence
        )
        signals.append(
            {
                "entity": entity.name,
                "snippet": ae.snippet or article.title,
                "url": article.url,
                "sentiment": ae.sentiment,
                "kind": "indirect",
                "path": best.entities,
                "weight": contribution,
                "path_strength": best.strength,
                "path_confidence": best.confidence,
            }
        )

    if not signals:
        return _build_result(
            security=security,
            verdict="INSUFFICIENT_DATA",
            confidence=0.0,
            horizon="medium",
            net_score=0.0,
            signals=[],
            quotes=None,
        )

    net_score = sum(s["weight"] for s in signals)
    positive = sum(s["weight"] for s in signals if s["weight"] > 0)
    negative = abs(sum(s["weight"] for s in signals if s["weight"] < 0))
    total = positive + negative
    agreement = abs(positive - negative) / total if total else 0.0

    from app.market.moex import MOEXClient
    quotes = await MOEXClient().fetch_quote(ticker)
    closes = await MOEXClient().fetch_daily_closes(ticker)

    indicator_note = None
    if quotes and closes:
        rsi_value = rsi(closes)
        ma20 = sma(closes, 20)
        if rsi_value is not None and rsi_value > 70 and net_score > 0:
            net_score *= 0.5
            indicator_note = f"RSI={rsi_value:.0f} — зона перекупленности, сигнал ослаблен"
        elif rsi_value is not None and rsi_value < 30 and net_score < 0:
            net_score *= 0.5
            indicator_note = f"RSI={rsi_value:.0f} — зона перепроданности, сигнал ослаблен"
        elif ma20 is not None and quotes["price"] < ma20:
            net_score *= 0.9

    coverage = min(len(signals) / 5.0, 1.0)
    confidence = max(0.0, min(0.95, agreement * 0.5 + coverage * 0.3 + 0.2))
    verdict = _verdict_for_score(net_score)
    if verdict != "HOLD" and confidence < MIN_CONFIDENCE_FOR_VERDICT:
        verdict = "HOLD"
    horizon = _horizon_for_score(net_score)

    result = _build_result(
        security=security,
        verdict=verdict,
        confidence=round(confidence, 2),
        horizon=horizon,
        net_score=net_score,
        signals=signals,
        quotes=quotes,
        indicator_note=indicator_note,
    )

    strategy = Strategy(
        security_id=security.id,
        verdict=verdict,
        horizon=horizon,
        confidence=_confidence_label(confidence),
        entry_price=result["strategy"]["levels"]["entry"],
        take_profit=result["strategy"]["levels"]["take_profit"],
        stop_loss=result["strategy"]["levels"]["stop_loss"],
        model_version="mvp-0.1",
        rationale_summary=result["rationale_summary"],
    )
    session.add(strategy)
    await session.flush()

    for s in signals[:10]:
        session.add(
            EvidenceItem(
                strategy_id=strategy.id,
                kind="news_fact" if s["kind"] == "direct" else "graph_path",
                quote=s["snippet"],
                url=s["url"],
                graph_path=s["path"] if s["kind"] == "indirect" else [],
                weight=round(s["weight"], 4),
            )
        )

    await session.commit()
    result["strategy_id"] = strategy.id
    return result


def _confidence_label(confidence: float) -> str:
    if confidence >= 0.7:
        return "high"
    if confidence >= 0.4:
        return "medium"
    return "low"


def _build_result(
    security: Security,
    verdict: str,
    confidence: float,
    horizon: str,
    net_score: float,
    signals: list[dict],
    quotes: dict | None,
    indicator_note: str | None = None,
) -> dict:
    entry = quotes["price"] if quotes else None
    if verdict == "BUY" and entry:
        take_profit = round(entry * 1.05, 2)
        stop_loss = round(entry * 0.95, 2)
    elif verdict == "SELL" and entry:
        take_profit = round(entry * 0.95, 2)
        stop_loss = round(entry * 1.05, 2)
    else:
        take_profit = None
        stop_loss = None

    reasons = []
    if verdict == "INSUFFICIENT_DATA":
        reasons.append("Недостаточно данных для уверенного вердикта")
    else:
        for s in signals[:5]:
            direction = "усиливает" if s["weight"] > 0 else "ослабляет"
            reasons.append(f"{s['entity']}: {direction} ({'+' if s['weight'] > 0 else ''}{s['weight']:.2f})")
    if indicator_note:
        reasons.append(indicator_note)

    return {
        "security": {
            "ticker": security.ticker,
            "name": security.name,
            "market": security.market,
            "sector": security.sector,
        },
        "strategy": {
            "verdict": verdict,
            "horizon": horizon,
            "confidence": confidence,
            "net_score": round(net_score, 3),
            "levels": {
                "entry": entry,
                "take_profit": take_profit,
                "stop_loss": stop_loss,
            },
        },
        "signals": signals,
        "quotes": quotes,
        "rationale_summary": "; ".join(reasons) or "Нет данных",
    }
