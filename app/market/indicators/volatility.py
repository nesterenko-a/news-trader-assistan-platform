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
