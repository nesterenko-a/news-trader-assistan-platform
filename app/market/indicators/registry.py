from app.market.indicators.oi import DEFAULT_PARAMS as OI_DEFAULT_PARAMS
from app.market.indicators.volume_profile import (
    DEFAULT_PARAMS as VOLUME_PROFILE_DEFAULT_PARAMS,
)
from app.market.indicators.ema import DEFAULT_PARAMS as EMA_DEFAULT_PARAMS
from app.market.indicators.macd import DEFAULT_PARAMS as MACD_DEFAULT_PARAMS
from app.market.indicators.support_resistance import (
    DEFAULT_PARAMS as SUPPORT_RESISTANCE_DEFAULT_PARAMS,
)
from app.market.indicators.bollinger import (
    DEFAULT_PARAMS as BOLLINGER_DEFAULT_PARAMS,
)
from app.market.indicators.atr import DEFAULT_PARAMS as ATR_DEFAULT_PARAMS
from app.market.indicators.adx import DEFAULT_PARAMS as ADX_DEFAULT_PARAMS

REGISTRY: dict[str, dict] = {
    "bollinger": {
        "title": "Полосы Боллинджера",
        "description": (
            "Средняя SMA(period) ± k·σ; %B и bandwidth; сигналы touch_upper/"
            "touch_lower, revert_in, squeeze (сжатие полос)"
        ),
        "params": BOLLINGER_DEFAULT_PARAMS,
        "complexity": "easy",
        "markets": ["stocks", "futures"],
    },
    "atr": {
        "title": "ATR (средний истинный диапазон)",
        "description": (
            "Волатильность (сглаживание Уайлдера); atr_pct — ATR в % от цены "
            "для сравнения бумаг; сигналов не даёт"
        ),
        "params": ATR_DEFAULT_PARAMS,
        "complexity": "easy",
        "markets": ["stocks", "futures"],
    },
    "adx": {
        "title": "ADX / DI",
        "description": (
            "Сила и направление тренда: +DI/−DI, ADX; сигналы trend "
            "(ADX≥25), range (ADX<20), bullish/bearish (пересечение DI)"
        ),
        "params": ADX_DEFAULT_PARAMS,
        "complexity": "medium",
        "markets": ["stocks", "futures"],
    },
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
    "support_resistance": {
        "title": "Поддержка/сопротивление",
        "description": (
            "Уровни поддержки и сопротивления (pivot points + локальные "
            "экстремумы + кластеризация по ATR); сигналы bounce (отскок) "
            "и breakout (пробой)"
        ),
        "params": SUPPORT_RESISTANCE_DEFAULT_PARAMS,
        "complexity": "hard",
        "markets": ["stocks", "futures"],
    },
}
