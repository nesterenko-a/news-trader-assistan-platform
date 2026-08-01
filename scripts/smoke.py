import asyncio
import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./smoke.db"
if os.path.exists("./smoke.db"):
    os.remove("./smoke.db")

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.connection import SessionLocal, engine, init_db
from app.db.models import Security, Strategy
from app.graph.service import seed_graph
from app.main import app


async def _seed() -> None:
    await init_db()
    async with SessionLocal() as session:
        await seed_graph(session)
        sber = await session.scalar(select(Security).where(Security.ticker == "SBER"))
        if sber is not None:
            session.add(
                Strategy(
                    security_id=sber.id,
                    verdict="HOLD",
                    horizon="medium",
                    confidence="low",
                    model_version="mvp-0.1",
                    rationale_summary="smoke seed",
                )
            )
            await session.commit()


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
        alerts_page = client.get("/alerts", headers=headers)
        print(
            "web alerts page:",
            alerts_page.status_code,
            "Алерты" in alerts_page.text,
            "Telegram-уведомления" in alerts_page.text,
        )
        link_bad = client.post(
            "/v1/alerts/telegram/link", json={"code": "INVALID"}, headers=headers
        )
        print("telegram link invalid:", link_bad.status_code)
        link_off = client.delete("/v1/alerts/telegram/link", headers=headers)
        print("telegram unlink:", link_off.status_code)

        history = client.get("/v1/strategies/history", headers=headers)
        hist_items = history.json()
        print("strategy history:", history.status_code, len(hist_items))
        first_id = hist_items[0]["id"] if hist_items else None
        fb = None
        if first_id:
            fb = client.post(
                f"/v1/strategies/{first_id}/feedback",
                json={"rating": "worked"},
                headers=headers,
            )
        print("feedback post:", fb.status_code if fb else "skip")
        fb_stats = client.get("/v1/strategies/feedback/stats", headers=headers)
        print("feedback stats:", fb_stats.status_code, fb_stats.json() if fb_stats.status_code == 200 else "")
        history_page = client.get("/history", headers=headers)
        print(
            "web history page:",
            history_page.status_code,
            "История стратегий" in history_page.text,
            "Сработало" in history_page.text,
        )

        macro = client.get("/v1/macro/calendar")
        print("macro calendar:", macro.status_code)
        macro_page = client.get("/macro")
        print("web macro page:", macro_page.status_code, "Макрокалендарь" in macro_page.text)
        sber_macro = client.get("/v1/macro/securities/SBER")
        print("macro by security:", sber_macro.status_code)
finally:
    asyncio.run(engine.dispose())
