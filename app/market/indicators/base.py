from dataclasses import dataclass, field
from datetime import date


@dataclass
class IndicatorValue:
    date: date
    value: float | None = None
    kind: str = "value"


@dataclass
class IndicatorSignal:
    date: date
    kind: str
    severity: str
    note: str


@dataclass
class IndicatorResult:
    indicator: str
    params: dict
    values: list[IndicatorValue]
    signals: list[IndicatorSignal]
    meta: dict = field(default_factory=dict)
