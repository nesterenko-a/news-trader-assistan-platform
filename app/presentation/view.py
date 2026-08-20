from dataclasses import dataclass, field

KIND_LABELS = {
    "direct": "прямой",
    "indirect": "косвенный",
    "oi": "OI (фьючерс)",
    "vp": "профиль объёма",
    "trend": "MACD (тренд)",
    "sr": "уровни S/R",
}


@dataclass
class LevelsView:
    entry: float | None = None
    take_profit: float | None = None
    stop_loss: float | None = None


@dataclass
class SignalView:
    entity: str
    kind: str
    kind_label: str
    sentiment: str
    weight: float
    weight_str: str
    path: str
    source_ref: str = ""


@dataclass
class NewsItemView:
    title: str
    url: str
    source_name: str
    date_str: str
    entity_tags: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class StrategyView:
    ticker: str
    name: str
    sector: str
    market: str
    verdict: str
    horizon: str
    confidence: float
    net_score: float
    levels: LevelsView = field(default_factory=LevelsView)
    rationale: str = ""
    signals: list[SignalView] = field(default_factory=list)
    news: list[NewsItemView] = field(default_factory=list)
    counterarguments: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    research: list[str] = field(default_factory=list)
    web_url: str = ""


def build_strategy_view(
    security,
    result: dict,
    web_url: str,
    news: list[NewsItemView] | None = None,
) -> StrategyView:
    strategy = result["strategy"]
    levels = strategy["levels"]
    signals = []
    research: list[str] = []
    for signal in result["signals"]:
        kind = signal["kind"]
        ref = signal.get("path_source_ref") or ""
        if ref and ref != "curated":
            research.append(ref)
            research = list(dict.fromkeys(research))  # уникальные
        signals.append(
            SignalView(
                entity=signal["entity"],
                kind=kind,
                kind_label=KIND_LABELS.get(kind, kind),
                sentiment=signal["sentiment"],
                weight=round(signal["weight"], 3),
                weight_str="—" if kind in ("oi", "vp", "trend", "sr") else f"{signal['weight']:+.3f}",
                path=" → ".join(signal["path"]),
                source_ref=ref,
            )
        )
    return StrategyView(
        ticker=security.ticker,
        name=security.name,
        sector=security.sector,
        market=security.market,
        verdict=strategy["verdict"],
        horizon=strategy["horizon"],
        confidence=strategy["confidence"],
        net_score=strategy["net_score"],
        levels=LevelsView(
            entry=levels.get("entry"),
            take_profit=levels.get("take_profit"),
            stop_loss=levels.get("stop_loss"),
        ),
        rationale=result["rationale_summary"],
        signals=signals,
        news=news or [],
        counterarguments=[ca["text"] for ca in result.get("counterarguments", [])],
        risks=result.get("risks", []),
        research=research,
        web_url=web_url,
    )
