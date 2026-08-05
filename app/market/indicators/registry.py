from app.market.indicators.oi import DEFAULT_PARAMS as OI_DEFAULT_PARAMS

REGISTRY: dict[str, dict] = {
    "oi": {
        "title": "Открытый интерес (OI)",
        "description": (
            "Открытые позиции фьючерсов (контракты) и сигналы «цена × OI»: "
            "Strong Bull / Strong Bear / Long Liquidation / Short Covering"
        ),
        "params": OI_DEFAULT_PARAMS,
        "complexity": "medium",
        "markets": ["futures"],
    },
}
