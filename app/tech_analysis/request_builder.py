"""Построение Markdown-запроса для «Теханализ в LLM».

Собирает актуальные данные по бумаге (акция или фьючерс) из БД и формирует
Markdown-запрос к ChatGPT: тикер/тип/цена, свечи трёх окон (1Y/3M/1M) с
объёмом, уровни поддержки/сопротивления, Volume Profile, RSI, BB/ATR/ADX/
EMA/MACD; для фьючерсов — OI, сигнал «цена × OI», клиентские группы и базис.

Промт берётся из файла (settings.TECH_ANALYSIS_PROMPT_FILE); при отсутствии
файла используется встроенный fallback.
"""

from datetime import date, timedelta

from sqlalchemy import select

from app.config import get_settings
from app.db.models import MarketCandle, Security
from app.market.indicators.adx import calculate_adx
from app.market.indicators.atr import calculate_atr
from app.market.indicators.basis_service import basis_for_ticker
from app.market.indicators.bollinger import calculate_bollinger
from app.market.indicators.ema import calculate_ema
from app.market.indicators.macd import calculate_macd
from app.market.indicators.rsi_indicator import calculate_rsi
from app.market.indicators.support_resistance import calculate_support_resistance
from app.market.indicators.volume_profile import calculate_volume_profile
from app.market.oi_data import client_groups_series, latest_oi_signal, nearest_future

FALLBACK_PROMPT = """Проведи технический анализ данной бумаги/фьючерса на основе предоставленных данных.

Мне нужен практический торговый разбор с конкретными уровнями входа, стопа и целями.

Не соглашайся автоматически, отделяй факты от предположений, учитывай текущую цену. Если данных недостаточно — прямо укажи.

Используй структуру ответа: сценарии A (основной), B (альтернативный), C (негативный),
и обязательно в конце добавь JSON-блок {verdict, entry, tp, sl, scenario_a, scenario_b, scenario_c}.
"""

WINDOWS = [
    (365, "1Y"),
    (90, "3M"),
    (30, "1M"),
]

CLIENT_POSITIONS_DAYS = 31
CLIENT_POSITIONS_MAX_ROWS = 22


async def _candles_window(
    session,
    security_id: int,
    days: int,
) -> list:
    return list(
        (
            await session.scalars(
                select(MarketCandle)
                .where(
                    MarketCandle.security_id == security_id,
                    MarketCandle.trading_date >= date.today() - timedelta(days=days),
                )
                .order_by(MarketCandle.trading_date)
            )
        ).all()
    )


def _fmt_price(v) -> str:
    return f"{v:g}" if v is not None else "—"


def _candles_md(candles: list, title: str) -> str:
    if not candles:
        return f"### {title}\nНет данных.\n"
    lines = [f"### {title} ({len(candles)} свечей)", "| Дата | Цена | Объём |", "|---|---|---:|"]
    for c in candles[-60:]:
        d = getattr(c, "trading_date", None) or getattr(c, "date", None)
        vol = getattr(c, "volume", None)
        lines.append(f"| {d} | {_fmt_price(c.close)} | {vol if vol is not None else '—'} |")
    return "\n".join(lines) + "\n"


def _client_positions_md(groups: list[dict]) -> str:
    recent = groups[-CLIENT_POSITIONS_MAX_ROWS:]
    first_date = recent[0].get("date")
    last_date = recent[-1].get("date")
    lines = [
        "### Клиентские позиции",
        (
            f"Последние известные позиции за {first_date} — {last_date} "
            f"({len(recent)} торговых дат; окно до {CLIENT_POSITIONS_DAYS} календарного дня)."
        ),
        "| Дата | Физ long | Физ short | Физ нетто | Юр long | Юр short | Юр нетто |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in recent:
        physical = row.get("physical") or {}
        juridical = row.get("juridical") or {}
        lines.append(
            f"| {row.get('date')} | {physical.get('long', '—')} | "
            f"{physical.get('short', '—')} | {physical.get('net', '—')} | "
            f"{juridical.get('long', '—')} | {juridical.get('short', '—')} | "
            f"{juridical.get('net', '—')} |"
        )
    return "\n".join(lines) + "\n"


async def build_analysis_request(session, security: Security) -> dict:
    """Собирает Markdown-запрос для LLM по выбранной бумаге."""
    settings = get_settings()
    prompt = FALLBACK_PROMPT
    try:
        with open(settings.tech_analysis_prompt_file, encoding="utf-8") as f:
            prompt = f.read()
    except (OSError, TypeError):
        pass

    is_future = security.security_type == "futures"
    parts: list[str] = [prompt, "\n\n---\n## ДАННЫЕ ДЛЯ АНАЛИЗА\n"]

    # Шапка
    parts.append(
        f"### Бумага\n- Тикер: {security.ticker}\n"
        f"- Название: {security.name}\n"
        f"- Тип: {'фьючерс' if is_future else 'акция'}\n"
        f"- Рынок: {security.market} · Валюта: {security.currency}\n"
    )
    if is_future and security.assetcode:
        parts.append(f"- Базовый актив (спот): {security.assetcode}\n")

    # Промт уже требует графики; здесь передаём данные таблично по трём окнам
    for days, title in WINDOWS:
        candles = await _candles_window(session, security.id, days)
        block = _candles_md(candles, title)
        parts.append(block)

    # Индикаторы на последнем дне (по данным окна 1Y)
    candles_year = await _candles_window(session, security.id, 365)
    price_at_analysis = None
    if candles_year:
        close_val = candles_year[-1].close if candles_year else None
        price_at_analysis = _fmt_price(close_val) if close_val is not None else None
        parts.append("### Индикаторы (последние значения)\n")
        if close_val is not None:
            parts.append(f"- Последняя цена: {_fmt_price(close_val)}\n")

        rsi = calculate_rsi(candles_year, params={"period": 14})
        parts.append(
            f"- RSI(14): {_fmt_price(rsi.meta.get('latest_rsi'))}"
            f" (состояние: {rsi.meta.get('state', '—')})\n"
        )

        sr = calculate_support_resistance(candles_year)
        levels = sr.meta.get("levels") or []
        if levels:
            lvl_str = ", ".join(
                f"{lv.get('price')} ({'сопр.' if lv.get('kind') == 'resistance' else 'поддержка'}, "
                f"сила {lv.get('strength')})"
                for lv in levels[:15]
            )
            parts.append(f"- Уровни S/R: {lvl_str}\n")

        vp = calculate_volume_profile(candles_year)
        vpm = vp.meta
        parts.append(
            f"- Volume Profile: POC={_fmt_price(vpm.get('poc'))}, "
            f"VAH={_fmt_price(vpm.get('vah'))}, VAL={_fmt_price(vpm.get('val'))}\n"
        )

        for name, module, params in (
            ("BB", calculate_bollinger, {"period": 20, "k": 2.0}),
            ("ATR", calculate_atr, {"period": 14}),
            ("ADX", calculate_adx, {"period": 14}),
            ("EMA", calculate_ema, {"fast": 12, "slow": 26}),
            ("MACD", calculate_macd, {"fast": 12, "slow": 26, "signal": 9}),
        ):
            res = module(candles_year, params=params)
            latest = None
            # возьмём последне значение из значений серии
            val = None
            for v in reversed(res.values):
                val = v.value
                break
            signal_kinds = [s.kind for s in res.signals]
            parts.append(
                f"- {name}: {_fmt_price(val)}"
                + (f" (сигналы: {', '.join(signal_kinds[:5])})" if signal_kinds else "")
                + "\n"
            )

    if is_future:
        # OI, сигнал «цена × OI», клиентские группы, базис
        oi_signal = await latest_oi_signal(session, security.id)
        if oi_signal:
            parts.append(
                f"- OI сигнал: {oi_signal.get('kind', '')}"
                f" — {oi_signal.get('note', '')}\n"
            )
        groups = await client_groups_series(
            session,
            security.id,
            from_date=date.today() - timedelta(days=CLIENT_POSITIONS_DAYS),
        )
        if groups:
            parts.append(_client_positions_md(groups))
        basis = await basis_for_ticker(session, security.ticker)
        if basis.values:
            parts.append(
                f"- Базис (фьючерс vs спот): {_fmt_price(basis.meta.get('latest_basis'))}"
                f" ({basis.meta.get('state', '—')}, "
                f"{_fmt_price(basis.meta.get('latest_basis_pct'))}%)\n"
            )

    request_md = "\n".join(parts)
    return {
        "request_md": request_md,
        "is_future": is_future,
        "security_name": security.name,
        "price_at_analysis": price_at_analysis,
    }
