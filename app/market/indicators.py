def sma(values: list[float], period: int) -> float | None:
    if len(values) < period or period <= 0:
        return None
    return sum(values[-period:]) / period


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


def volatility(values: list[float], period: int = 20) -> float | None:
    if len(values) < period + 1:
        return None
    window = values[-(period + 1):]
    returns = []
    for i in range(len(window) - 1):
        if window[i] != 0:
            returns.append((window[i + 1] - window[i]) / window[i])
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return variance ** 0.5
