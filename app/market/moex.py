from datetime import date, timedelta

import httpx

from app.config import get_settings

settings = get_settings()


class MOEXClient:
    def __init__(self, base_url: str | None = None):
        self.base_url = base_url or settings.moex_base_url

    async def fetch_quote(self, ticker: str) -> dict | None:
        url = f"{self.base_url}/engines/stock/markets/shares/boards/TQBR/securities.json"
        params = {
            "iss.meta": "off",
            "iss.only": "marketdata",
            "marketdata.columns": "SECID,LAST,OPEN,HIGH,LOW,VOLUME",
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        columns = data.get("marketdata", {}).get("columns", [])
        for row in data.get("marketdata", {}).get("data", []):
            record = dict(zip(columns, row))
            if record.get("SECID") == ticker and record.get("LAST") is not None:
                return {
                    "ticker": ticker,
                    "price": float(record["LAST"]),
                    "open": float(record["OPEN"]) if record.get("OPEN") else None,
                    "high": float(record["HIGH"]) if record.get("HIGH") else None,
                    "low": float(record["LOW"]) if record.get("LOW") else None,
                    "volume": int(record["VOLUME"]) if record.get("VOLUME") else 0,
                }
        return None

    async def fetch_daily_closes(self, ticker: str, days: int = 60) -> list[float]:
        bars = await self.fetch_daily_bars(ticker, days)
        return [close for _, close in bars]

    async def fetch_daily_bars(
        self, ticker: str, days: int = 120
    ) -> list[tuple[date, float]]:
        candles = await self.fetch_candles(
            ticker,
            from_date=date.today() - timedelta(days=days),
            till_date=date.today(),
        )
        return [(c["date"], c["close"]) for c in candles]

    async def fetch_open_positions(
        self,
        ticker: str,
        from_date: date,
        till_date: date,
    ) -> list[dict]:
        """История открытых позиций фьючерса: iss/history/.../forts/securities/{SECID}.json"""
        url = (
            f"{self.base_url}/history/engines/futures/markets/forts/"
            f"securities/{ticker}.json"
        )
        params = {
            "iss.only": "history",
            "history.columns": (
                "TRADEDATE,CLOSE,VOLUME,OPENPOSITION,OPENPOSITIONVALUE,SHORTNAME"
            ),
            "from": from_date.isoformat(),
            "till": till_date.isoformat(),
        }
        rows = []
        start = 0
        page_size = 500
        while True:
            params["start"] = str(start)
            params["limit"] = str(page_size)
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()

            block = data.get("history", {})
            columns = block.get("columns", [])
            page_rows = block.get("data", [])
            for row in page_rows:
                record = dict(zip(columns, row))
                tradedate = record.get("TRADEDATE")
                if not tradedate:
                    continue
                try:
                    row_date = date.fromisoformat(tradedate)
                except ValueError:
                    continue
                rows.append(
                    {
                        "date": row_date,
                        "close": (
                            float(record["CLOSE"])
                            if record.get("CLOSE") is not None
                            else None
                        ),
                        "volume": (
                            int(record["VOLUME"]) if record.get("VOLUME") else 0
                        ),
                        "open_position": (
                            int(record["OPENPOSITION"])
                            if record.get("OPENPOSITION") is not None
                            else 0
                        ),
                        "open_position_value": (
                            float(record["OPENPOSITIONVALUE"])
                            if record.get("OPENPOSITIONVALUE") is not None
                            else None
                        ),
                        "shortname": record.get("SHORTNAME") or "",
                    }
                )
            if len(page_rows) < page_size:
                break
            start += len(page_rows)

        rows.sort(key=lambda r: r["date"])
        return rows

    async def fetch_candles(
        self,
        ticker: str,
        from_date: date,
        till_date: date,
        interval: int = 24,
    ) -> list[dict]:
        url = (
            f"{self.base_url}/engines/stock/markets/shares/boards/TQBR/"
            f"securities/{ticker}/candles.json"
        )
        params = {
            "iss.only": "candles",
            "candles.columns": "begin,open,high,low,close,volume",
            "interval": str(interval),
            "from": from_date.isoformat(),
            "till": till_date.isoformat(),
        }
        candles = []
        start = 0
        page_size = 500
        while True:
            params["start"] = str(start)
            params["limit"] = str(page_size)
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()

            block = data.get("candles", {})
            columns = block.get("columns", [])
            rows = block.get("data", [])
            for row in rows:
                record = dict(zip(columns, row))
                begin = record.get("begin")
                if not begin:
                    continue
                try:
                    bar_date = date.fromisoformat(begin[:10])
                except ValueError:
                    continue
                candles.append(
                    {
                        "date": bar_date,
                        "open": float(record["open"]) if record.get("open") is not None else None,
                        "high": float(record["high"]) if record.get("high") is not None else None,
                        "low": float(record["low"]) if record.get("low") is not None else None,
                        "close": float(record["close"]) if record.get("close") is not None else None,
                        "volume": int(record["volume"]) if record.get("volume") else 0,
                    }
                )
            if len(rows) < page_size:
                break
            start += len(rows)

        candles.sort(key=lambda c: c["date"])
        return candles
