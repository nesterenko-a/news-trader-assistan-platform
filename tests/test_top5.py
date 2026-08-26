"""Тесты Top-5: сервис батча Теханализа группы акций и ранжирование.

Не требуют сети/LLM: тестируется детерминированная логика выбора лучшей
стратегии по Expected R, построение рейтинга top5, валидация шаблона
(kind=stock) и API-маршрутов.
"""

import json

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy import select

from app.db.connection import Base
from app.db.models import FuturesTemplate, Security, TechAnalysis, User
from app.tech_analysis.batch import (
    _ticker_list,
    best_strategy,
    fresh_analysis,
    get_stock_template,
    top5,
)
from app.tech_analysis.parser import expected_r, parse_response


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as store:
        yield store
    await engine.dispose()


def _scenario_json(prob, rr, title="sc"):
    sc = {"title": title, "entry": "100-105", "stop": "98", "targets": "110/115", "why": "w",
          "probability": prob, "rr": rr}
    return json.dumps({"scenario_a": sc, "scenario_b": {}, "scenario_c": {}}, ensure_ascii=False)


def test_expected_r_formula():
    # 0.60 × 2.5 − 0.40 × 1 = 1.10
    assert expected_r(0.6, 2.5) == pytest.approx(1.10)
    assert expected_r(0.5, 2.0) == pytest.approx(0.5)
    assert expected_r(None, 2.0) is None
    assert expected_r(0.6, None) is None


def test_parse_response_includes_rr_and_expected_r():
    md = '```json\n{"scenario_a":{"probability":0.65,"rr":2.5}}\n```\n'
    parsed = parse_response(md)
    assert parsed["scenario_a"]["rr"] == 2.5
    assert parsed["scenario_a"]["probability"] == 0.65
    # 0.65 × 2.5 − 0.35 × 1 = 1.275
    assert parsed["scenario_a"]["expected_r"] == pytest.approx(1.275)


def test_ticker_list():
    tpl = FuturesTemplate(name="t", tickers="AFLT; SBER, Gazp", kind="stock")
    assert set(_ticker_list(tpl)) == {"AFLT", "SBER", "GAZP"}


def test_compute_rr_fallback_from_levels():
    from app.tech_analysis.batch import compute_rr

    # без явного rr: entry 100-105 (средняя 102.5), stop 98 (риск 4.5), target 110 (цель 7.5) → rr ~1.667
    sc = {"entry": "100-105", "stop": "98", "targets": "110"}
    assert compute_rr(sc) is not None
    assert compute_rr(sc) == pytest.approx(7.5 / 4.5, abs=0.01)
    # явный rr имеет приоритет
    sc2 = {"entry": "100-105", "stop": "98", "targets": "110", "rr": 2.5}
    assert compute_rr(sc2) == 2.5
    # нет уровней → None
    assert compute_rr({"entry": "", "stop": "", "targets": ""}) is None


def test_compute_risk_pct():
    from app.tech_analysis.batch import compute_risk_pct

    sc = {"entry": "100-105", "stop": "98"}
    assert compute_risk_pct(sc) == pytest.approx(4.5 * 100.0 / 102.5, abs=0.01)
    assert compute_risk_pct({"entry": "", "stop": ""}) is None


def test_compute_score_and_qualified():
    from app.tech_analysis.batch import _deal_qualified, compute_score

    sc = {"entry": "100", "stop": "98", "targets": "110/120", "why": "поддержка+отскок",
          "probability": 0.65, "rr": 2.5}
    s = compute_score(sc)
    assert s is not None and 0 <= s <= 100
    # сильная сделка проходит фильтр качества
    row = {"rr": 2.5, "risk": 2.0, "probability": 0.65}
    assert _deal_qualified(row) is True
    row_bad = {"rr": 1.2, "risk": 2.0, "probability": 0.65}
    assert _deal_qualified(row_bad) is False


async def test_get_stock_template_kind(session):
    t_fut = FuturesTemplate(name="f", tickers="W4V6", kind="futures")
    t_stock = FuturesTemplate(name="s", tickers="AFLT", kind="stock")
    session.add_all([t_fut, t_stock])
    await session.flush()
    assert await get_stock_template(session, t_stock.id) is t_stock
    assert await get_stock_template(session, t_fut.id) is None


def test_best_strategy_picks_max_expected_r():
    items = [
        {"ticker": "SBER", "id": 1, "verdict": "BUY", "scenario_json": _scenario_json(0.6, 2.5)},
        {"ticker": "SBER", "id": 2, "verdict": "SELL", "scenario_json": _scenario_json(0.7, 3.0)},
        {"ticker": "SBER", "id": 3, "verdict": "HOLD", "scenario_json": _scenario_json(0.9, 5.0)},
    ]
    best = best_strategy(items)
    # SELL (0.7×3 − 0.3×1 = 1.8) > BUY (1.1); HOLD игнорируется
    assert best["dir"] == "SHORT"
    assert best["expected_r"] == pytest.approx(1.8)
    assert best["strategy"] == "scenario_a"


def test_best_strategy_none_when_no_valid():
    # пустой JSON (нет prob/rr) → None
    items = [{"ticker": "X", "id": 1, "verdict": "BUY", "scenario_json": "{}"}]
    assert best_strategy(items) is None
    # WAIT теперь учитывается (выжидать), чтобы список не был пуст
    items = [{"ticker": "X", "id": 1, "verdict": "WAIT", "scenario_json": _scenario_json(0.6, 2.5)}]
    best = best_strategy(items)
    assert best is not None
    assert best["dir"] == "WAIT"


async def test_top5_ranks_by_expected_r(session):
    session.add_all(
        [
            FuturesTemplate(name="tpl", tickers="AAA,BBB", kind="stock"),
            Security(ticker="AAA", name="Акция А", security_type="stock"),
            Security(ticker="BBB", name="Акция Б", security_type="stock"),
        ]
    )
    await session.flush()
    template = await session.scalar(select(FuturesTemplate).where(FuturesTemplate.name == "tpl"))
    from datetime import datetime, timedelta

    ago = datetime.utcnow() - timedelta(hours=1)
    # BBB — лучший Expected R (0.8×3 − 0.2 = 2.2); AAA — хуже (0.5×2 − 0.5 = 0.5)
    session.add(
        TechAnalysis(
            ticker="AAA", status="success", stage="done", verdict="BUY",
            scenario_json=_scenario_json(0.5, 2.0), finished_at=ago,
        )
    )
    session.add(
        TechAnalysis(
            ticker="BBB", status="success", stage="done", verdict="BUY",
            scenario_json=_scenario_json(0.8, 3.0), finished_at=ago,
        )
    )
    await session.flush()
    res = await top5(session, template.id)
    assert res["total"] == 2
    assert res["items"][0]["ticker"] == "BBB"
    assert res["items"][1]["ticker"] == "AAA"
    assert res["items"][0]["expected_r"] == pytest.approx(2.2)


async def test_fresh_analysis_reuse(session):
    from datetime import datetime, timedelta

    session.add(TechAnalysis(ticker="SBER", status="success", stage="done",
                             scenario_json=_scenario_json(0.6, 2.5),
                             finished_at=datetime.utcnow()))
    await session.flush()
    fresh = await fresh_analysis(session, "SBER")
    assert fresh is not None and fresh.status == "success"


async def test_list_batches_includes_top5(session):
    """list_batches возвращает историю с собственным Top-5 батча (включая WAIT)."""
    from datetime import datetime

    from app.db.models import TechAnalysisBatch
    from app.tech_analysis.batch import list_batches

    tpl = FuturesTemplate(name="lt", tickers="SBER", kind="stock")
    session.add(tpl)
    await session.flush()
    batch = TechAnalysisBatch(user_id=None, template_id=tpl.id, status="success")
    session.add(batch)
    await session.flush()
    session.add(
        TechAnalysis(
            ticker="SBER", status="success", stage="done", verdict="WAIT",
            scenario_json=_scenario_json(0.5, 2.0), finished_at=datetime.utcnow(),
            batch_id=batch.id,
        )
    )
    await session.flush()

    rows = await list_batches(session, tpl.id)
    assert len(rows) == 1
    assert rows[0]["status"] == "success"
    assert len(rows[0]["top5"]) == 1
    assert rows[0]["top5"][0]["ticker"] == "SBER"
    assert rows[0]["top5"][0]["dir"] == "WAIT"


async def test_top5_page_renders(session):
    """Страница /top5 рендерит Top-5 и сценарии (без сети/LLM)."""
    from fastapi import Request

    from app.auth import create_session
    from app.web.router import top5_page

    user = User(username="u1", password_hash="x")
    session.add(user)
    await session.flush()
    token = await create_session(session, user)
    tpl = FuturesTemplate(name="tp", tickers="AAA", kind="stock")
    session.add(tpl)
    session.add(Security(ticker="AAA", name="Акция А", security_type="stock"))
    await session.flush()
    from datetime import datetime

    from app.db.models import TechAnalysisBatch

    batch = TechAnalysisBatch(user_id=user.id, template_id=tpl.id, status="success")
    session.add(batch)
    await session.flush()
    session.add(
        TechAnalysis(
            ticker="AAA", status="success", stage="done", verdict="BUY",
            scenario_json=_scenario_json(0.6, 2.5), finished_at=datetime.utcnow(),
            batch_id=batch.id,
        )
    )
    await session.flush()

    req = Request(
        {
            "type": "http", "method": "GET", "path": "/top5",
            "headers": [(b"cookie", f"nt_token={token}".encode())],
            "server": ("test", 80), "query_string": b"", "client": ("test", 80),
            "scheme": "http",
        }
    )
    resp = await top5_page(req, session, template_id=tpl.id)
    html = resp.body.decode()
    assert "Отбор сделок — Top 5" in html
    assert "AAA" in html
    # селектор выбора LLM (Авто/ChatGPT/DeepSeek)
    assert "LLM для анализа" in html
    assert "btn-provider" in html
    # макет: фильтры, метрики, панель «Как считать Score»
    assert "По Score" in html
    assert "По R/R" in html
    assert "По риску" in html
    assert "Как считать Score" in html
    assert "Показана одна лучшая стратегия" in html
    # история запусков батчей
    assert "История запусков Top-5" in html
    assert "batch-detail-" in html

