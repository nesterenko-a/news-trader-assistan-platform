"""Тесты Top-5: сервис батча Теханализа группы акций и ранжирование.

Не требуют сети/LLM: тестируется детерминированная логика выбора лучшей
стратегии по Expected R, построение рейтинга top5, валидация шаблона
(kind=stock) и API-маршрутов.
"""

import asyncio
import json

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy import select

from app.db.connection import Base
from app.db.models import FuturesTemplate, MarketCandle, Security, TechAnalysis, User
from app.tech_analysis.batch import (
    _ticker_list,
    _liquidity_by_ticker,
    best_strategy,
    fresh_analysis,
    get_instrument_template,
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


def test_parse_response_await_confirmation():
    md = '```json\n{"verdict":"BUY","await_confirmation":true,"scenario_a":{"probability":0.65,"rr":2.5}}\n```\n'
    parsed = parse_response(md)
    assert parsed["await_confirmation"] is True
    md2 = '```json\n{"verdict":"BUY","scenario_a":{"probability":0.65,"rr":2.5}}\n```\n'
    assert parse_response(md2)["await_confirmation"] is False


def test_best_strategy_carries_await_confirmation():
    from app.tech_analysis.batch import best_strategy

    items = [{"ticker": "X", "id": 1, "verdict": "BUY", "await_confirmation": True,
              "scenario_json": _scenario_json(0.6, 2.5)}]
    best = best_strategy(items)
    assert best is not None
    assert best["await_confirmation"] is True
    items2 = [{"ticker": "X", "id": 1, "verdict": "BUY", "await_confirmation": False,
               "scenario_json": _scenario_json(0.6, 2.5)}]
    assert best_strategy(items2)["await_confirmation"] is False


def test_ticker_list():
    tpl = FuturesTemplate(name="t", tickers="AFLT; SBER, Gazp", kind="stock")
    assert set(_ticker_list(tpl)) == {"AFLT", "SBER", "GAZP"}


async def test_batch_starts_all_thirty_tickers(session, monkeypatch):
    from app.db.models import TechAnalysisBatch
    from app.tech_analysis import batch as batch_module

    tickers = [f"T{i:02d}" for i in range(30)]
    template = FuturesTemplate(name="thirty", tickers=",".join(tickers), kind="stock")
    session.add(template)
    await session.flush()
    captured = []

    async def record_batches(batch_ids, force):
        captured.extend(batch_ids)

    monkeypatch.setattr(batch_module, "_run_batches", record_batches)
    batches = await batch_module.start_batch(session, template.id)
    await asyncio.sleep(0)
    assert len(batches) == 3
    assert captured == [batch.id for batch in batches]
    assert [batch.status for batch in batches] == ["queued"] * 3
    assert [len((await session.scalars(select(TechAnalysis).where(TechAnalysis.batch_id == batch.id))).all()) for batch in batches] == [10, 10, 10]
    rows = (await session.scalars(select(TechAnalysis).where(TechAnalysis.batch_id.in_(captured)).order_by(TechAnalysis.id))).all()
    assert [row.ticker for row in rows] == tickers
    assert len((await session.scalars(select(TechAnalysisBatch).where(TechAnalysisBatch.template_id == template.id))).all()) == 3
    progress = await batch_module.batch_progress(session, batches[2].id)
    assert progress["total"] == 10
    assert progress["queued"] == 10
    assert progress["status"] == "queued"


async def test_batch_allows_template_above_old_limit(session, monkeypatch):
    from app.tech_analysis import batch as batch_module

    template = FuturesTemplate(name="too many", tickers=",".join(f"T{i:02d}" for i in range(30)), kind="stock")
    session.add(template)
    await session.flush()
    async def no_run(batch_ids, force):
        return None

    monkeypatch.setattr(batch_module, "_run_batches", no_run)
    assert len(await batch_module.start_batch(session, template.id)) == 3


@pytest.mark.parametrize("security_type", ["stock", "futures"])
async def test_restart_failed_batch_analysis_from_first_stage(session, monkeypatch, security_type):
    from types import SimpleNamespace

    from app.db.models import TechAnalysisBatch
    from app.tech_analysis import service

    batch = TechAnalysisBatch(status="failed")
    session.add(batch)
    await session.flush()
    row = TechAnalysis(ticker="AAA", status="failed", stage="done", error="сбой данных", batch_id=batch.id)
    session.add(row)
    await session.flush()
    analysis_id = row.id
    started = []

    async def fake_security(store, ticker):
        return Security(ticker=ticker, security_type=security_type)

    async def fake_analyze(row_id, ticker):
        started.append((row_id, ticker))

    monkeypatch.setattr(service, "_load_security", fake_security)
    monkeypatch.setattr(service, "resolve_llm", lambda provider: SimpleNamespace(api_key="test", model="test-model"))
    monkeypatch.setattr(service, "_analyze", fake_analyze)
    try:
        restarted = await service.restart_batch_analysis(session, analysis_id)
        await asyncio.sleep(0)
        assert restarted.id == analysis_id
        assert restarted.status == "running"
        assert restarted.stage == "refreshing_data"
        assert restarted.is_future is (security_type == "futures")
        assert restarted.error is None
        assert started == [(analysis_id, "AAA")]
    finally:
        service._active.pop("AAA", None)


async def test_retry_api_rejects_other_batch_and_keeps_analysis_id(session, monkeypatch):
    from fastapi import HTTPException

    from app.api.routes import top5 as top5_routes
    from app.db.models import TechAnalysisBatch

    batch = TechAnalysisBatch(status="failed")
    other = TechAnalysisBatch(status="failed")
    session.add_all([batch, other])
    await session.flush()
    row = TechAnalysis(ticker="AAA", status="failed", stage="done", batch_id=batch.id)
    session.add(row)
    await session.flush()
    with pytest.raises(HTTPException) as error:
        await top5_routes.top5_retry_item(other.id, row.id, session, None)
    assert error.value.status_code == 404

    async def fake_restart(store, analysis_id):
        current = await store.get(TechAnalysis, analysis_id)
        current.status = "running"
        current.stage = "refreshing_data"
        return current

    monkeypatch.setattr(top5_routes, "restart_batch_analysis", fake_restart)
    response = await top5_routes.top5_retry_item(batch.id, row.id, session, None)
    assert response == {"id": row.id, "status": "running", "stage": "refreshing_data"}
    assert (await session.get(TechAnalysisBatch, batch.id)).status == "running"
    with pytest.raises(HTTPException) as error:
        await top5_routes.top5_retry_item(batch.id, row.id, session, None)
    assert error.value.status_code == 409


async def test_batches_finish_one_group_before_starting_next(monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.db.connection as dbconn
    import app.tech_analysis.batch as batch_module
    from app.db.models import TechAnalysisBatch

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        for batch_number in range(2):
            batch = TechAnalysisBatch(status="queued")
            session.add(batch)
            await session.flush()
            for ticker_number in range(10):
                session.add(TechAnalysis(
                    ticker=f"T{batch_number}{ticker_number}", status="queued", stage="queued", batch_id=batch.id,
                ))
        await session.commit()
    monkeypatch.setattr(dbconn, "SessionLocal", factory)
    seen = []

    async def finish_immediately(session, analysis_id):
        row = await session.get(TechAnalysis, analysis_id)
        if row.batch_id == 2:
            assert (await session.get(TechAnalysisBatch, 1)).status == "partial"
        seen.append((row.batch_id, row.ticker))
        if row.ticker == "T03":
            raise RuntimeError("временный сбой")
        row.status = "success"
        row.stage = "done"
        await session.commit()

    monkeypatch.setattr(batch_module, "restart_batch_analysis", finish_immediately)
    await batch_module._run_batches([1, 2], force=True)
    assert [batch_id for batch_id, _ in seen] == [1] * 10 + [2] * 10
    async with factory() as session:
        assert (await session.get(TechAnalysisBatch, 1)).status == "partial"
        assert (await session.get(TechAnalysisBatch, 2)).status == "success"
        failed = await session.scalar(select(TechAnalysis).where(TechAnalysis.ticker == "T03"))
        assert failed.status == "failed"
        assert failed.error == "временный сбой"
    await engine.dispose()


async def test_retry_completion_updates_batch_without_page_poll(monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.db.connection as dbconn
    from app.db.models import TechAnalysisBatch
    from app.tech_analysis.service import _sync_batch_status

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(dbconn, "SessionLocal", factory)
    monkeypatch.setattr("app.tech_analysis.service.SessionLocal", factory)
    async with factory() as session:
        batch = TechAnalysisBatch(status="running")
        session.add(batch)
        await session.flush()
        row = TechAnalysis(ticker="AAA", status="success", stage="done", batch_id=batch.id)
        session.add(row)
        await session.commit()
        batch_id = batch.id
        await _sync_batch_status(row.id)
        session.expire_all()
        assert (await session.get(TechAnalysisBatch, batch_id)).status == "success"
    await engine.dispose()


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
    assert _deal_qualified({"rr": 2.5, "risk": 2.0, "probability": None}) is False
    assert _deal_qualified({"dir": "WAIT", "rr": 2.5, "risk": 2.0, "probability": 0.65}) is False


async def test_liquidity_requires_ten_recent_valid_candles(session):
    from datetime import datetime, timedelta

    good = Security(ticker="GOOD", name="Good", security_type="stock")
    sparse = Security(ticker="SPARSE", name="Sparse", security_type="stock")
    session.add_all([good, sparse])
    await session.flush()
    today = datetime.utcnow().date()
    for index in range(10):
        session.add(MarketCandle(
            security_id=good.id, trading_date=today - timedelta(days=index),
            close=100, volume=20_000,
        ))
    session.add(MarketCandle(security_id=good.id, trading_date=today - timedelta(days=11), close=100, volume=0))
    session.add(MarketCandle(security_id=sparse.id, trading_date=today - timedelta(days=46), close=100, volume=20_000))
    session.add(MarketCandle(security_id=sparse.id, trading_date=today + timedelta(days=1), close=100, volume=20_000))
    await session.flush()
    result = await _liquidity_by_ticker(session, ["GOOD", "SPARSE"])
    assert result == {"GOOD": 2_000_000}


def test_best_strategy_marks_matching_confirmation_plan():
    data = json.loads(_scenario_json(0.6, 2.5))
    data["confirmation_plans"] = {"a": {
        "direction": "buy", "steps": [
            {"kind": "approach", "label": "Подход", "detail": "Подойти к уровню"},
            {"kind": "entry", "label": "Вход", "detail": "После закрепления"},
        ],
    }}
    analysis = {"ticker": "X", "id": 1, "verdict": "BUY", "scenario_json": json.dumps(data)}
    assert best_strategy([analysis])["has_confirmation_plan"] is True
    analysis["verdict"] = "SELL"
    assert best_strategy([analysis])["has_confirmation_plan"] is False


async def test_get_instrument_template_kind(session):
    t_fut = FuturesTemplate(name="f", tickers="W4V6", kind="futures")
    t_stock = FuturesTemplate(name="s", tickers="AFLT", kind="stock")
    session.add_all([t_fut, t_stock])
    await session.flush()
    assert await get_instrument_template(session, t_stock.id) is t_stock
    assert await get_instrument_template(session, t_fut.id) is t_fut


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
    assert best_strategy([{"ticker": "X", "id": 1, "verdict": "BUY", "scenario_json": "[]"}]) is None
    assert best_strategy([{"ticker": "X", "id": 1, "verdict": "BUY", "scenario_json": '{"scenario_a": []}'}]) is None
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
    # макет: кликабельные заголовки-сортировки и всплывающие подсказки
    assert 'data-col="rr"' in html
    assert 'data-col="risk"' in html
    assert 'data-col="score"' in html
    assert "Как считать Score" in html
    assert "R/R" in html and "Риск" in html and "риск до стопа" in html
    assert "Фильтр качества сделки" in html
    assert "top5-filter-liquidity" in html
    assert "Показана одна лучшая стратегия" in html
    # легенда значка «ожидание подтверждения»
    assert "ждём подхода цены к точке входа" in html
    # история запусков батчей
    assert "История запусков Top-5" in html
    assert "batch-detail-" in html



async def test_run_batch_force_skips_fresh_reuse(session, monkeypatch):
    """force=True игнорирует свежесть: не переиспользует актуальный анализ."""
    from datetime import datetime

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.db.connection as dbconn
    import app.tech_analysis.batch as batch_mod
    from app.db.models import TechAnalysisBatch

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    from app.db.connection import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as s:
        s.add(TechAnalysis(ticker="AAA", status="success", stage="done", verdict="BUY",
                           scenario_json=_scenario_json(0.6, 2.5),
                           finished_at=datetime.utcnow()))
        for _ in range(2):
            batch = TechAnalysisBatch(status="queued")
            s.add(batch)
            await s.flush()
            s.add(TechAnalysis(ticker="AAA", status="queued", stage="queued", batch_id=batch.id))
        await s.commit()

    monkeypatch.setattr(dbconn, "SessionLocal", factory)

    started = []
    async def fake_restart(session, analysis_id):
        row = await session.get(TechAnalysis, analysis_id)
        started.append(row.ticker)
        row.status = "success"
        row.stage = "done"
        await session.commit()
        return row
    monkeypatch.setattr(batch_mod, "restart_batch_analysis", fake_restart)

    await batch_mod._run_batches([1], force=False)
    assert started == []
    async with factory() as s:
        assert (await s.get(TechAnalysisBatch, 1)).status == "success"
        assert (await s.get(TechAnalysis, 2)).status == "success"
        assert (await s.get(TechAnalysis, 1)).batch_id is None

    await batch_mod._run_batches([2], force=True)
    assert started == ["AAA"]

    await engine.dispose()


def test_best_strategy_carries_distinct_scenarios_and_price():
    """Правки Top-5 (docs/25): раскрытие строки — все 3 разных сценария (как «Полный разбор»),
    а не один общий; текущая цена и время прогноза передаются в запись."""
    from datetime import datetime

    sc_a = {"title": "A основной", "entry": "100-105", "stop": "98", "targets": "110", "why": "wa",
            "probability": 0.65, "rr": 2.5}
    sc_b = {"title": "B альтернативный", "entry": "95", "stop": "92", "targets": "105", "why": "wb",
            "probability": 0.25, "rr": 1.8}
    sc_c = {"title": "C негативный", "entry": "", "stop": "", "targets": "", "why": "wc",
            "probability": 0.10, "rr": 0.5}
    ana = {
        "ticker": "X",
        "id": 1,
        "name": "X Inc",
        "verdict": "BUY",
        "await_confirmation": False,
        "price": 101.5,
        "as_of": datetime(2026, 8, 1, 12, 0),
        "scenario_json": json.dumps(
            {"scenario_a": sc_a, "scenario_b": sc_b, "scenario_c": sc_c},
            ensure_ascii=False,
        ),
    }
    best = best_strategy([ana])
    assert best is not None
    assert best["price"] == 101.5
    assert best["as_of"] == datetime(2026, 8, 1, 12, 0)
    # три разных сценария A/B/C (не один общий)
    sc = best["scenarios"]
    assert sc["a"]["title"] == "A основной"
    assert sc["b"]["title"] == "B альтернативный"
    assert sc["c"]["title"] == "C негативный"
    assert sc["a"]["entry"] == "100-105"
    assert sc["b"]["entry"] == "95"
    # лучший сценарий (макс. Expected R) — сценарий A
    assert best["strategy"] == "scenario_a"
    assert best["entry_reference"] == 102.5
    assert best["first_target"] == 110.0
    assert best["entry"] == "100-105"


def test_parse_final_assessment():
    from app.tech_analysis.parser import parse_final_assessment

    md = """## 11. Итоговая оценка

### Итоговая оценка

Направление: 🟢 BUY

Среднесрочно: 7/10 в пользу роста
Краткосрочно: 6/10 в пользу роста
Качество точки входа сейчас: 7/10

Лучший вход:
3180-3220

Стоп:
3100

Цели:
3300 / 3400 / 3500

Ключевой уровень:
3200

Главный риск:
Внезапное решение ОПЕК+ по добыче

Моя рекомендация:
Я бы ждал отката к 3180-3220 и входил после подтверждения покупателя.

```json
{"verdict":"BUY"}
```
"""
    fa = parse_final_assessment(md)
    assert "3200" in fa["key_level"]
    assert "ОПЕК" in fa["main_risk"]
    assert "3180-3220" in fa["recommendation"]
    # пустой ответ → пустые значения
    assert parse_final_assessment("") == {"key_level": "", "main_risk": "", "recommendation": ""}
