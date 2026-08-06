import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Article,
    ArticleEntity,
    EvidenceItem,
    MarketCandle,
    Security,
    Strategy,
)
from app.strategy.weights import get_latest
from app.graph.service import (
    DIRECTION_SIGN,
    STRENGTH_WEIGHT,
    find_influence_paths,
    security_entity_ids,
)
from app.market.indicators import rsi, sma, volatility
from app.market.indicators.volume_profile import calculate_volume_profile
from app.market.indicators.macd import calculate_macd
from app.market.oi_data import latest_oi_signal, nearest_future

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


def _oi_contradicts(kind: str, verdict: str) -> bool:
    """Противоречит ли сигнал OI вердикту (контраргумент)."""
    if verdict == "BUY":
        return kind in ("strong_bear", "long_liquidation")
    if verdict == "SELL":
        return kind in ("strong_bull", "short_covering")
    return False


def _oi_sentiment(kind: str) -> str:
    """Знак OI-сигнала для отображения в «Сигналах»."""
    if kind in ("strong_bull", "short_covering"):
        return "positive"
    if kind in ("strong_bear", "long_liquidation"):
        return "negative"
    return "neutral"


def _oi_verdict_label(kind: str) -> str:
    """Вердикт по индикатору OI (семантическая трактовка сигнала): покупка/продажа/удержание."""
    if kind in ("strong_bull", "long_liquidation", "bullish_setup"):
        return "покупка"
    if kind in ("strong_bear", "short_covering", "bearish_setup"):
        return "продажа"
    return "удержание"


async def _build_counterarguments(
    session: AsyncSession,
    signals: list[dict],
    verdict: str,
    indicator_note: str | None,
    oi_signal: dict | None = None,
) -> tuple[list[dict], list[str]]:
    counterarguments: list[dict] = []
    risks: list[str] = []

    if verdict in ("BUY", "SELL"):
        if verdict == "BUY":
            counter_signals = [s for s in signals if s["weight"] < 0]
        else:
            counter_signals = [s for s in signals if s["weight"] > 0]
        for signal in counter_signals:
            direction = "усиливает" if signal["weight"] > 0 else "ослабляет"
            text = f"{signal['entity']}: {direction} ({signal['weight']:+.2f})"
            counterarguments.append(
                {"entity": signal["entity"], "text": text, "weight": signal["weight"]}
            )
            risks.append(f"отраслевой/корпоративный: {text}")
        if indicator_note:
            counterarguments.append(
                {"entity": "индикаторы", "text": indicator_note, "weight": 0.0}
            )
            risks.append(f"рыночный: {indicator_note}")

    if oi_signal:
        oi_text = (
            f"OI (открытый интерес): {oi_signal['note']}"
            f" — Вердикт по индикатору: {_oi_verdict_label(oi_signal['kind'])}"
        )
        risks.append(f"рыночный: {oi_text}")
        if verdict in ("BUY", "SELL") and _oi_contradicts(
            oi_signal["kind"], verdict
        ):
            counterarguments.append(
                {"entity": "OI (открытый интерес)", "text": oi_text, "weight": 0.0}
            )

    from app.macro.service import list_events

    now = datetime.now(timezone.utc)
    until = now + timedelta(days=7)
    events = await list_events(session, since=now, until=until)
    for event in events:
        if event.expected_impact != "high":
            continue
        text = f"ближайшее макрособытие: {event.title} ({event.event_time.strftime('%d.%m')})"
        risks.append(f"событийный: {text}")
        if verdict in ("BUY", "SELL"):
            counterarguments.append(
                {"entity": "макрособытия", "text": text, "weight": 0.0}
            )
    return counterarguments, risks


async def generate_strategy(
    session: AsyncSession,
    ticker: str,
    as_of: datetime | None = None,
    persist: bool = True,
    use_live_market: bool = True,
) -> dict:
    now = as_of or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
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
            .where(
                Article.published_at >= since,
                Article.published_at <= now,
            )
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

    weights_version, factors = await get_latest(session)
    news_factor = factors.get("news", 1.0)
    graph_factor = factors.get("graph", 1.0)
    for s in signals:
        if s["kind"] == "direct":
            s["weight"] *= news_factor
        elif s["kind"] == "indirect":
            s["weight"] *= graph_factor

    net_score = sum(s["weight"] for s in signals)
    positive = sum(s["weight"] for s in signals if s["weight"] > 0)
    negative = abs(sum(s["weight"] for s in signals if s["weight"] < 0))
    total = positive + negative
    agreement = abs(positive - negative) / total if total else 0.0

    indicator_note = None
    if use_live_market:
        from app.market.moex import MOEXClient

        quotes = await MOEXClient().fetch_quote(ticker)
        closes = await MOEXClient().fetch_daily_closes(ticker)

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
    else:
        quotes = None

    oi_signal = None
    oi_future = None
    if security.security_type == "futures":
        oi_signal = await latest_oi_signal(session, security.id, as_of=now.date())
    else:
        oi_future = await nearest_future(session, security.ticker, as_of=now.date())
        if oi_future is not None:
            oi_signal = await latest_oi_signal(session, oi_future.id, as_of=now.date())
    if oi_signal:
        kind = oi_signal["kind"]
        vol = oi_signal.get("volume")
        bearish = kind in ("strong_bear", "long_liquidation")
        bullish = kind in ("strong_bull", "short_covering")
        if bearish and net_score > 0:
            net_score *= 0.85 if vol == "up" else 0.92 if vol == "down" else 0.85
        elif bullish and net_score < 0:
            net_score *= 0.85 if vol == "up" else 0.92 if vol == "down" else 0.85
        elif bullish and net_score > 0 and vol == "up":
            net_score *= 1.05
        elif bearish and net_score < 0 and vol == "up":
            net_score *= 1.05
        oi_note = oi_signal["note"]
    else:
        oi_note = None
    if oi_note:
        indicator_note = (
            f"OI — {oi_note}"
            if not indicator_note
            else f"{indicator_note}; OI — {oi_note}"
        )
    if oi_signal and oi_future is not None:
        signals.append(
            {
                "entity": f"OI {oi_future.ticker}",
                "snippet": "",
                "url": "",
                "sentiment": _oi_sentiment(oi_signal["kind"]),
                "kind": "oi",
                "path": [oi_future.name or oi_future.ticker],
                "weight": 0.0,
            }
        )

    vp_candles = (
        await session.scalars(
            select(MarketCandle)
            .where(
                MarketCandle.security_id == security.id,
                MarketCandle.trading_date <= now.date(),
            )
            .order_by(MarketCandle.trading_date)
        )
    ).all()
    vp_meta = calculate_volume_profile(
        vp_candles, params={"period": 60}
    ).meta
    if vp_meta.get("nodes"):
        signals.append(
            {
                "entity": "Профиль объёма",
                "snippet": "",
                "url": "",
                "sentiment": "neutral",
                "kind": "vp",
                "path": [
                    f"POC {vp_meta['poc']:.2f} · "
                    f"Value Area {vp_meta['val']:.2f}–{vp_meta['vah']:.2f}"
                ],
                "weight": 0.0,
            }
        )
        last_close = vp_candles[-1].close if vp_candles else None
        if last_close is not None:
            vah, val = vp_meta["vah"], vp_meta["val"]
            if last_close > vah and net_score > 0:
                net_score *= 0.9
                indicator_note = (
                    f"цена {last_close:.2f} выше Value Area (VAH={vah:.2f}) — "
                    f"перекупленность по профилю объёма"
                    if not indicator_note
                    else f"{indicator_note}; цена выше Value Area (VAH={vah:.2f}) — перекупленность"
                )
            elif last_close < val and net_score < 0:
                net_score *= 0.9
                indicator_note = (
                    f"цена {last_close:.2f} ниже Value Area (VAL={val:.2f}) — "
                    f"перепроданность по профилю объёма"
                    if not indicator_note
                    else f"{indicator_note}; цена ниже Value Area (VAL={val:.2f}) — перепроданность"
                )

    # MACD: направление тренда как техсигнал (шаг 5 ТЗ §8.2)
    trend_candles = [c for c in vp_candles if c.close is not None][-200:]
    if len(trend_candles) >= 40:
        macd_meta = calculate_macd(trend_candles).meta
        trend = macd_meta.get("trend")
        if trend in ("up", "down"):
            signals.append(
                {
                    "entity": "MACD (тренд)",
                    "snippet": "",
                    "url": "",
                    "sentiment": "positive" if trend == "up" else "negative",
                    "kind": "trend",
                    "path": [
                        "MACD "
                        + ("> signal — тренд вверх" if trend == "up" else "< signal — тренд вниз")
                    ],
                    "weight": 0.0,
                }
            )
            if trend == "down" and net_score > 0:
                net_score *= 0.9
                indicator_note = (
                    "тренд по MACD вниз — сигнал ослаблен"
                    if not indicator_note
                    else f"{indicator_note}; тренд по MACD вниз — сигнал ослаблен"
                )
            elif trend == "up" and net_score < 0:
                net_score *= 0.9
                indicator_note = (
                    "тренд по MACD вверх — сигнал ослаблен"
                    if not indicator_note
                    else f"{indicator_note}; тренд по MACD вверх — сигнал ослаблен"
                )

    coverage = min(len(signals) / 5.0, 1.0)
    confidence = max(0.0, min(0.95, agreement * 0.5 + coverage * 0.3 + 0.2))
    verdict = _verdict_for_score(net_score)
    if verdict != "HOLD" and confidence < MIN_CONFIDENCE_FOR_VERDICT:
        verdict = "HOLD"
    horizon = _horizon_for_score(net_score)

    counterarguments, risks = await _build_counterarguments(
        session, signals, verdict, indicator_note, oi_signal=oi_signal
    )
    if counterarguments:
        counter_weight = sum(
            abs(ca["weight"]) for ca in counterarguments if ca["weight"]
        )
        if abs(net_score) > 0 and counter_weight >= 0.3 * abs(net_score):
            penalty = 0.85 * factors.get("counter_penalty", 1.0)
            confidence = max(0.0, min(0.95, confidence * penalty))

    result = _build_result(
        security=security,
        verdict=verdict,
        confidence=round(confidence, 2),
        horizon=horizon,
        net_score=net_score,
        signals=signals,
        quotes=quotes,
        indicator_note=indicator_note,
        counterarguments=counterarguments,
        risks=risks,
    )
    result["weights_version"] = weights_version

    if persist:
        model_version = (
            f"mvp-0.1-w{weights_version}" if weights_version else "mvp-0.1"
        )
        strategy = Strategy(
            security_id=security.id,
            verdict=verdict,
            horizon=horizon,
            confidence=_confidence_label(confidence),
            entry_price=result["strategy"]["levels"]["entry"],
            take_profit=result["strategy"]["levels"]["take_profit"],
            stop_loss=result["strategy"]["levels"]["stop_loss"],
            model_version=model_version,
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

        for ca in counterarguments:
            if ca["text"]:
                session.add(
                    EvidenceItem(
                        strategy_id=strategy.id,
                        kind="counterargument",
                        quote=ca["text"],
                        weight=round(ca["weight"], 4),
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
    counterarguments: list[dict] | None = None,
    risks: list[str] | None = None,
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
        "counterarguments": counterarguments or [],
        "risks": risks or [],
        "rationale_summary": "; ".join(reasons) or "Нет данных",
    }
