"""Сервис расчёта базиса «фьючерс против спота» для API/веба/запроса LLM.

Оркестрирует загрузку свечей спота и фьючерса из БД и вызывает
app.market.indicators.basis.calculate_basis. Принимает тикер акции или
фьючерсного контракта.

Определение пары:
- если тикер — акция (security_type == "stock"), то фьючерс = оi_data.nearest_future;
- если тикер — фьючерс, то спот = Security.ticker == фьючерс.assetcode.
"""

from datetime import date, timedelta

from sqlalchemy import select

from app.db.models import MarketCandle, Security
from app.market.indicators.basis import DEFAULT_PARAMS, calculate_basis
from app.market.indicators.base import IndicatorResult
from app.market.oi_data import nearest_future


async def _load_closes(session, security_id: int, days: int) -> list[tuple[date, float]]:
    rows = (
        await session.scalars(
            select(MarketCandle)
            .where(
                MarketCandle.security_id == security_id,
                MarketCandle.trading_date >= date.today() - timedelta(days=days),
                MarketCandle.close.is_not(None),
            )
            .order_by(MarketCandle.trading_date)
        )
    ).all()
    return [(c.trading_date, c.close) for c in rows]


async def basis_for_ticker(
    session,
    ticker: str,
    as_of: date | None = None,
    days: int = 120,
    params: dict | None = None,
) -> IndicatorResult:
    """Базис по тикеру акции или фьючерса (фьючерс vs спот).

    Возвращает IndicatorResult с meta (latest_basis, latest_basis_pct, state)
    или пустой результат (note) если пару не удалось определить / нет данных.
    """
    ticker = ticker.upper()
    as_of = as_of or date.today()
    security = await session.scalar(
        select(Security).where(Security.ticker == ticker)
    )
    if security is None:
        return IndicatorResult(
            indicator="basis", params={**DEFAULT_PARAMS}, values=[], signals=[],
            meta={"note": "бумага не найдена"},
        )

    spot_ticker = None
    if security.security_type == "futures":
        spot_ticker = security.assetcode
        future = security
    else:
        future = await nearest_future(session, ticker, as_of=as_of)
        if future is None:
            return IndicatorResult(
                indicator="basis", params={**DEFAULT_PARAMS}, values=[], signals=[],
                meta={"note": "для акции не найден фьючерс", "state": "no_future"},
            )
        spot_ticker = ticker

    spot = await session.scalar(
        select(Security).where(Security.ticker == spot_ticker)
    ) if spot_ticker else None
    if spot is None:
        return IndicatorResult(
            indicator="basis", params={**DEFAULT_PARAMS}, values=[], signals=[],
            meta={"note": "спот не найден", "state": "no_spot"},
        )

    future_prices = await _load_closes(session, future.id, days)
    spot_prices = await _load_closes(session, spot.id, days)
    return calculate_basis(future_prices, spot_prices, params=params)


__all__ = ["basis_for_ticker"]
