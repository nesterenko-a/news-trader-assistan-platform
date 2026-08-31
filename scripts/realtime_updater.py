"""Демон актуализации рыночных данных в реальном времени (docs/24).

Периодически обновляет:
  - live-котировки (LAST/OHLC/объём) по акциям (watchlist+mvp) и фьючерсам
    выбранного шаблона (RealTimeQuote, upsert);
  - дневные свечи акций и фьючерсов (sync_security_prices);
  - OI фьючерсов шаблона, включая группы клиентов (sync_security_oi).

Настройки (realtime_config) перечитываются каждую итерацию — тумблер/
интервалы в админке применяются без перезапуска. Если enabled=false — выход.

Запуск: python -m scripts.realtime_updater (или из админки как SCRIPTS).
Таймаут не ограничивается: демон работает, пока его не остановят.
"""

import argparse
import asyncio
import time

from app.db.connection import SessionLocal, init_db
from app.market.moex import MOEXClient
from app.market.oi_data import sync_security_oi
from app.market.prices import sync_security_prices
from app.market.realtime import compute_scope, ensure_config, fetch_scope_quotes
from app.notices.monitor import set_source_notice


def _quote_log_line(security, quote: dict | None, error: Exception | None) -> str:
    if error is not None:
        return f"[realtime] котировка {security.ticker}: ошибка {type(error).__name__}"
    if quote is None:
        return f"[realtime] котировка {security.ticker}: данных нет"
    return (
        f"[realtime] котировка {security.ticker}: обновлена "
        f"LAST={quote.get('price')}, объём={quote.get('volume')}"
    )


async def _set_moex_notice(active: bool) -> None:
    try:
        async with SessionLocal() as session:
            await set_source_notice(
                session,
                "realtime_moex",
                "warning",
                (
                    "Реальное время: MOEX недоступен — актуализация рыночных "
                    "данных приостановлена"
                ),
                active=active,
            )
    except Exception:
        pass


async def run_once() -> None:
    """Один проход цикла демона (читает настройки, обновляет данные). Возвращает интервал сна."""
    async with SessionLocal() as session:
        config = await ensure_config(session)
        if not config.enabled:
            print("Realtime отключён", flush=True)
            return None
        stocks, futures = await compute_scope(session, config)
        scope = list(stocks) + list(futures)
        started = time.monotonic()
        print(
            f"[realtime] проход: акции={len(stocks)}, фьючерсы={len(futures)}; "
            f"инструменты: {', '.join(s.ticker for s in scope) or 'нет'}",
            flush=True,
        )

        # Обновляем live-котировки
        quotes_updated = await fetch_scope_quotes(
            session,
            stocks,
            futures,
            on_result=lambda security, quote, error: print(
                _quote_log_line(security, quote, error), flush=True
            ),
        )

        # Свечи акций и фьючерсов
        candles_inserted = 0
        for security in list(stocks) + list(futures):
            inserted = await sync_security_prices(session, security.ticker)
            candles_inserted += inserted
            print(
                f"[realtime] свечи {security.ticker}: "
                f"{'добавлено ' + str(inserted) if inserted else 'актуальны'}",
                flush=True,
            )

        # OI фьючерсов (свечи + OI + группы клиентов)
        oi_inserted = 0
        if futures:
            client = MOEXClient()
            try:
                futures_list = await client.fetch_futures_list()
                futures_meta = {
                    f["secid"]: {
                        "assetcode": f.get("assetcode"),
                        "lastdeldate": f.get("lastdeldate"),
                    }
                    for f in futures_list
                }
            except Exception:
                futures_meta = {}
            cache: dict = {}
            for security in futures:
                inserted = await sync_security_oi(
                    session,
                    security.ticker,
                    futures_meta=futures_meta,
                    client_groups_cache=cache,
                )
                oi_inserted += inserted
                print(
                    f"[realtime] OI {security.ticker}: "
                    f"{'добавлено ' + str(inserted) if inserted else 'актуален или данных нет'}",
                    flush=True,
                )

        await _set_moex_notice(False)
        sleep_for = min(config.interval_quotes_sec, config.interval_candles_sec, config.interval_oi_sec)
        print(
            f"[realtime] проход завершён: котировок {quotes_updated}/{len(scope)}, "
            f"свечей добавлено {candles_inserted}, OI добавлено {oi_inserted}, "
            f"время {time.monotonic() - started:.1f} c; следующий проход через {sleep_for} c",
            flush=True,
        )
        return sleep_for


async def main() -> None:
    parser = argparse.ArgumentParser(description="Realtime-демон актуализации рыночных данных")
    parser.add_argument(
        "--oneshot",
        action="store_true",
        help="выполнить один проход и выйти (для проверки)",
    )
    args = parser.parse_args()

    await init_db()
    print("Realtime-демон запущен", flush=True)
    try:
        while True:
            try:
                sleep_for = await run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(
                    f"[realtime] ошибка прохода: {type(exc).__name__}: {exc}",
                    flush=True,
                )
                await _set_moex_notice(True)
                sleep_for = 30
            if sleep_for is None:
                break
            if args.oneshot:
                break
            await asyncio.sleep(max(5, min(sleep_for, 60)))
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("Realtime-демон остановлен (Ctrl+C)", flush=True)
    finally:
        await _set_moex_notice(False)


if __name__ == "__main__":
    asyncio.run(main())
