from app.market.indicators.oi import DEFAULT_PARAMS as OI_DEFAULT_PARAMS
from app.market.indicators.volume_profile import (
    DEFAULT_PARAMS as VOLUME_PROFILE_DEFAULT_PARAMS,
)
from app.market.indicators.ema import DEFAULT_PARAMS as EMA_DEFAULT_PARAMS
from app.market.indicators.macd import DEFAULT_PARAMS as MACD_DEFAULT_PARAMS

REGISTRY: dict[str, dict] = {
    "ema": {
        "title": "EMA (скользящие средние)",
        "description": (
            "Экспоненциальные скользящие средние (fast/slow) и сигналы "
            "пересечения: cross_up (golden cross) / cross_down (death cross)"
        ),
        "params": EMA_DEFAULT_PARAMS,
        "complexity": "easy",
        "markets": ["stocks", "futures"],
    },
    "macd": {
        "title": "MACD",
        "description": (
            "Схождение/расхождение скользящих средних: MACD, signal, "
            "гистограмма; сигналы cross_up/cross_down и hist_positive/negative"
        ),
        "params": MACD_DEFAULT_PARAMS,
        "complexity": "easy",
        "markets": ["stocks", "futures"],
    },
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
