def rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) < period + 1 or period <= 0:
        return None
    window = values[-(period + 1):]
    gains = 0.0
    losses = 0.0
    for i in range(len(window) - 1):
        change = window[i + 1] - window[i]
        if change >= 0:
            gains += change
        else:
            losses -= change
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100.0 - (100.0 / (1.0 + rs))
