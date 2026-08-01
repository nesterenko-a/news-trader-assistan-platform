import asyncio
import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./smoke.db"
if os.path.exists("./smoke.db"):
    os.remove("./smoke.db")

from fastapi.testclient import TestClient

from app.db.connection import SessionLocal, engine, init_db
from app.graph.service import seed_graph
from app.main import app


async def _seed() -> None:
    await init_db()
    async with SessionLocal() as session:
        await seed_graph(session)


asyncio.run(_seed())

try:
    with TestClient(app) as client:
        health = client.get("/v1/health")
        print("health:", health.status_code, health.json())

        securities = client.get("/v1/securities")
        print("securities:", securities.status_code, [s["ticker"] for s in securities.json()])

        search = client.get("/v1/securities/search", params={"q": "аэро"})
        print("search 'аэро':", search.status_code, [s["ticker"] for s in search.json()])

        strategy = client.post("/v1/securities/AFLT/strategy")
        body = strategy.json()
        print("strategy AFLT:", strategy.status_code, body["strategy"]["verdict"])

        unknown = client.post("/v1/securities/XXXX/strategy")
        print("strategy unknown:", unknown.status_code)

        index = client.get("/")
        print("web index:", index.status_code, "NewsTrader" in index.text)

        page = client.get("/securities/AFLT")
        print("web AFLT page:", page.status_code, "AFLT" in page.text)

        css = client.get("/static/style.css")
        print("web css:", css.status_code)

        search_web = client.get("/securities", params={"ticker": "AFLT"})
        print("web search redirect:", search_web.status_code, "AFLT" in search_web.text)

        filtered = client.get("/", params={"sector": "Авиаперевозки"})
        print(
            "web sector filter:",
            filtered.status_code,
            "AFLT" in filtered.text,
            "LKOH" not in filtered.text,
        )

        chart_page = client.get("/securities/AFLT", params={"range": "all"})
        print(
            "web chart block:",
            chart_page.status_code,
            "История цены" in chart_page.text,
            "chart-range" in chart_page.text,
            ">Всё</a>" in chart_page.text,
        )

        page = client.get("/securities/AFLT")
        print(
            "web screenshot btn:",
            page.status_code,
            "Сделать скриншот" in page.text,
            "app.js" in page.text,
        )

        register = client.post(
            "/v1/auth/register", json={"username": "smoke", "password": "secret123"}
        )
        print("auth register:", register.status_code)
        token = register.json().get("token", "")
        headers = {"Authorization": f"Bearer {token}"}

        wl_add = client.post("/v1/watchlist", json={"ticker": "AFLT"}, headers=headers)
        print("watchlist add:", wl_add.status_code)
        wl_list = client.get("/v1/watchlist", headers=headers)
        print(
            "watchlist list:",
            wl_list.status_code,
            any(i["ticker"] == "AFLT" for i in wl_list.json()),
        )

        pf_add = client.post(
            "/v1/portfolio",
            json={"ticker": "SBER", "quantity": 10, "avg_price": 100},
            headers=headers,
        )
        print("portfolio add:", pf_add.status_code)
        pf_list = client.get("/v1/portfolio", headers=headers)
        print(
            "portfolio list:",
            pf_list.status_code,
            any(p["ticker"] == "SBER" for p in pf_list.json()),
        )
        print("unauthorized portfolio:", client.get("/v1/portfolio").status_code)

        web_login = client.get("/login")
        print("web login page:", web_login.status_code, "Вход" in web_login.text)
        wl_page = client.get("/watchlist", follow_redirects=False)
        print("web watchlist unauth redirect:", wl_page.status_code)

        alerts = client.get("/v1/alerts", headers=headers)
        print("alerts list:", alerts.status_code)
        alert_settings = client.put(
            "/v1/alerts/settings", json={"min_impact": 0.5}, headers=headers
        )
        print("alerts settings:", alert_settings.status_code)
        alerts_page = client.get("/alerts")
        print("web alerts page:", alerts_page.status_code, "Алерты" in alerts_page.text)

        macro = client.get("/v1/macro/calendar")
        print("macro calendar:", macro.status_code)
        macro_page = client.get("/macro")
        print("web macro page:", macro_page.status_code, "Макрокалендарь" in macro_page.text)
        sber_macro = client.get("/v1/macro/securities/SBER")
        print("macro by security:", sber_macro.status_code)
finally:
    asyncio.run(engine.dispose())
