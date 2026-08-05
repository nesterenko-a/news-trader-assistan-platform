from app.market.indicators.oi import DEFAULT_PARAMS as OI_DEFAULT_PARAMS
from app.market.indicators.volume_profile import (
    DEFAULT_PARAMS as VOLUME_PROFILE_DEFAULT_PARAMS,
)

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
    "volume_profile": {
        "title": "Профиль объёма (Volume Profile)",
        "description": (
            "Распределение объёма по ценовым уровням за период: "
            "POC / Value Area (VAH, VAL) / HVN / LVN"
        ),
        "params": VOLUME_PROFILE_DEFAULT_PARAMS,
        "complexity": "medium",
        "markets": ["stocks", "futures"],
    },
}
