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
        url = (
            f"{self.base_url}/engines/stock/markets/shares/boards/TQBR/"
            f"securities/{ticker}/candles.json"
        )
        today = date.today()
        params = {
            "iss.meta": "off",
            "iss.only": "candles",
            "candles.columns": "close",
            "interval": "24",
            "from": (today - timedelta(days=days)).isoformat(),
            "to": today.isoformat(),
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        columns = data.get("candles", {}).get("columns", [])
        closes = []
        for row in data.get("candles", {}).get("data", []):
            record = dict(zip(columns, row))
            value = record.get("close")
            if value is not None:
                closes.append(float(value))
        return closes
