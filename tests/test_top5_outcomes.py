import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.connection import Base, get_session
from app.db.models import ScenarioEvaluation, ScenarioEvent, ScenarioSnapshot, TechAnalysis, TechAnalysisBatch, User
from app.tech_analysis.outcomes import capture, list_outcomes, summary, transition, validate_plan, watch_snapshots
from app.tech_analysis.outcome_worker import evaluate_pending
from app.tech_analysis.outcome_alerts import deliver_pending, queue_deliveries, set_subscription

T = datetime(2026, 10, 1, 7, tzinfo=timezone.utc)
PLAN = {"direction": "buy", "kind": "touch", "entry": 100.0, "stop": 95.0, "target": 110.0}
SCENARIO = {"entry": "100", "stop": "95", "targets": "110/115"}


def bar(hour, o=101, h=104, low=99, c=102):
    return {"begin_at": T + timedelta(hours=hour), "end_at": T + timedelta(hours=hour + 1) - timedelta(seconds=1), "open": o, "high": h, "low": low, "close": c, "volume": 100}


def evaluation():
    return ScenarioEvaluation(snapshot_id=1, state="pending", confirmed=False, complete=False)


def run(e, bars, plan=None, days=7, coverage=True):
    return transition(e, plan or PLAN, bars, T, T + timedelta(days=days), T + timedelta(days=8), coverage)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as store:
        yield store
    await engine.dispose()


async def seed(session):
    user = User(username="outcomes", password_hash="unused")
    session.add(user)
    await session.flush()
    batch = TechAnalysisBatch(user_id=user.id)
    session.add(batch)
    await session.flush()
    analysis = TechAnalysis(user_id=user.id, batch_id=batch.id, ticker="SBER", status="success", is_future=False, finished_at=T, model="test", provider="test", scenario_json=json.dumps({"scenario_a": SCENARIO, "execution_plans": {"a": PLAN}}))
    session.add(analysis)
    await session.flush()
    await capture(session, analysis)
    await session.commit()
    return user, analysis


def test_validate_rejects_text_mismatch_and_nonfinite():
    assert validate_plan(PLAN, SCENARIO)[1] == ""
    for field in ("entry", "stop", "target"):
        assert validate_plan({**PLAN, field: float("nan")}, SCENARIO)[1]
    assert validate_plan({**PLAN, "target": 120}, SCENARIO)[1]
    assert validate_plan({**PLAN, "direction": "wait"}, SCENARIO)[1]
    assert validate_plan({**PLAN, "kind": "retest"}, SCENARIO)[1]
    assert validate_plan(PLAN, SCENARIO, {"steps": []})[1]


def test_long_target_and_r():
    e = evaluation()
    events = run(e, [bar(1), bar(2, o=103, h=111, low=102, c=110)])
    assert e.state == "target"
    assert e.result_r == 2
    assert [event[0] for event in events] == ["open", "target"]


def test_short_target():
    e = evaluation()
    run(e, [bar(1, o=99, h=101, low=97, c=98), bar(2, o=98, h=99, low=89, c=90)], {**PLAN, "direction": "sell", "stop": 105, "target": 90})
    assert e.state == "target"
    assert e.result_r == 2


@pytest.mark.parametrize("bars", [
    [bar(1, o=101, h=111, low=99, c=110)],
    [bar(1), bar(2, o=100, h=111, low=94, c=101)],
    [bar(1, o=99, h=102, low=98, c=101)],
])
def test_ambiguous_not_counted_as_profit(bars):
    e = evaluation()
    run(e, bars)
    assert e.state == "ambiguous"
    assert e.result_r is None


def test_gap_stop_uses_open():
    e = evaluation()
    run(e, [bar(1), bar(2, o=90, h=93, low=89, c=92)])
    assert e.state == "stop"
    assert e.exit_price == 90
    assert e.result_r == -2


def test_confirmation_enters_next_bar_not_confirming_bar():
    e = evaluation()
    events = run(e, [bar(1), bar(2, o=103, h=106, low=102, c=104)], {**PLAN, "kind": "close_above", "trigger": 101})
    assert [event[0] for event in events][:2] == ["confirmed", "open"]
    assert e.entry_price == 103


def test_no_lookahead_or_partial_publication_bar():
    e = evaluation()
    run(e, [bar(0, o=100, h=111, low=90, c=100), bar(-1)], coverage=False)
    assert e.state == "pending"
    assert e.entry_price is None


def test_expiration_reuses_last_processed_close():
    e = evaluation()
    bars = [bar(1)]
    transition(e, PLAN, bars, T, T + timedelta(days=7), T + timedelta(hours=4))
    assert e.state == "open"
    run(e, bars)
    assert e.state == "expired_open"
    assert e.exit_price == 102


def test_missing_coverage_does_not_expire():
    e = evaluation()
    run(e, [], coverage=False)
    assert e.state == "pending"
    assert not e.complete


def test_cancel_and_entry_same_candle_are_ambiguous():
    e = evaluation()
    run(e, [bar(1, low=94, c=94)], {**PLAN, "cancel": {"kind": "close_below", "level": 96}})
    assert e.state == "ambiguous"


async def test_capture_is_immutable_idempotent_and_owned(session):
    user, analysis = await seed(session)
    analysis.scenario_json = "{}"
    await capture(session, analysis)
    await session.commit()
    assert await session.scalar(select(func.count()).select_from(ScenarioSnapshot)) == 3
    report = await list_outcomes(session, user.id)
    assert report["total"] == 3
    assert len([item for item in report["items"] if item["eligible"]]) == 1
    assert (await list_outcomes(session, user.id + 1))["total"] == 0
    await watch_snapshots(session, analysis.id, user.id)
    await session.commit()
    assert (await list_outcomes(session, user.id))["total"] == 3


async def test_reuse_grants_watch_without_duplicate_snapshot(session):
    _, analysis = await seed(session)
    other = User(username="other", password_hash="unused")
    session.add(other)
    await session.flush()
    await watch_snapshots(session, analysis.id, other.id)
    await session.commit()
    assert (await list_outcomes(session, other.id))["total"] == 3
    assert await session.scalar(select(func.count()).select_from(ScenarioSnapshot)) == 3


async def test_worker_replay_and_outage(session):
    user, _ = await seed(session)
    class Client:
        async def fetch_candles(self, *args, **kwargs):
            assert kwargs["include_time"] and kwargs["interval"] == 60
            return [bar(1), bar(2, o=103, h=111, low=102, c=110)]
    await evaluate_pending(session, client=Client(), now=T + timedelta(hours=4))
    await evaluate_pending(session, client=Client(), now=T + timedelta(hours=5))
    assert await session.scalar(select(func.count()).select_from(ScenarioEvent)) == 2
    stats = await summary(session, user.id)
    assert stats["mean_r"] == 2
    assert stats["entered"] == 1
    assert stats["target_rate"] == 1


async def test_worker_failure_preserves_state(session):
    user, _ = await seed(session)
    class Client:
        async def fetch_candles(self, *args, **kwargs):
            raise RuntimeError("network")
    await evaluate_pending(session, client=Client(), now=T + timedelta(days=9))
    report = await list_outcomes(session, user.id)
    active = next(item for item in report["items"] if item["eligible"])
    assert active["state"] == "pending"
    assert not active["freshness"]["data_complete"]


async def test_subscriptions_no_history_and_delivery_retry(session):
    user, _ = await seed(session)
    snapshot = await session.scalar(select(ScenarioSnapshot).where(ScenarioSnapshot.eligible.is_(True)))
    old = ScenarioEvent(snapshot_id=snapshot.id, kind="confirmed", occurred_at=T, recorded_at=T, data={})
    session.add(old)
    await session.commit()
    await set_subscription(session, user, snapshot.id, ["web"], ["confirmed", "open"])
    now = datetime.now(timezone.utc) + timedelta(seconds=1)
    session.add(ScenarioEvent(snapshot_id=snapshot.id, kind="open", occurred_at=now, recorded_at=now, data={"price": 100}))
    await session.commit()
    await queue_deliveries(session)
    await queue_deliveries(session)
    assert await deliver_pending(session, now=now) == 1
    assert await deliver_pending(session, now=now) == 0
    from app.tech_analysis.outcome_alerts import web_notifications
    assert len(await web_notifications(session, user.id)) == 1


async def test_api_ownership_and_csrf(session):
    from fastapi import FastAPI
    from app.api.routes.outcomes import router
    from app.auth import get_current_user
    user, _ = await seed(session)
    application = FastAPI()
    application.include_router(router, prefix="/v1")
    async def get_store():
        yield session
    application.dependency_overrides[get_session] = get_store
    application.dependency_overrides[get_current_user] = lambda: user
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        response = await client.get("/v1/top5/outcomes")
        assert response.status_code == 200
        snapshot_id = next(item["id"] for item in response.json()["items"] if item["eligible"])
        assert (await client.get("/v1/top5/outcomes/summary")).status_code == 200
        assert (await client.get("/v1/top5/outcomes/items/99999")).status_code == 404
        assert (await client.get(f"/v1/top5/outcomes/items/{snapshot_id}")).status_code == 200
        assert (await client.post(f"/v1/top5/outcomes/items/{snapshot_id}/subscription", json={})).status_code == 403
        assert (await client.post(f"/v1/top5/outcomes/items/{snapshot_id}/subscription", json={}, headers={"Origin": "http://test"})).status_code == 200


async def test_telegram_retry_and_disable_cancels_delivery(session):
    from app.db.models import ScenarioDelivery
    user, _ = await seed(session)
    user.telegram_chat_id = 123
    snapshot = await session.scalar(select(ScenarioSnapshot).where(ScenarioSnapshot.eligible.is_(True)))
    await set_subscription(session, user, snapshot.id, ["telegram"], ["open"])
    now = datetime.now(timezone.utc) + timedelta(seconds=1)
    session.add(ScenarioEvent(snapshot_id=snapshot.id, kind="open", occurred_at=now, recorded_at=now, data={"price": 100}))
    await session.commit()
    await queue_deliveries(session)
    async def failing(chat, text):
        raise RuntimeError("temporary")
    assert await deliver_pending(session, sender=failing, now=now) == 0
    delivery = await session.scalar(select(ScenarioDelivery))
    assert delivery.status == "pending" and delivery.attempts == 1
    assert await deliver_pending(session, sender=failing, now=now) == 0
    assert delivery.attempts == 1
    await set_subscription(session, user, snapshot.id, [], [])
    await deliver_pending(session, sender=failing, now=now + timedelta(minutes=5))
    assert delivery.status == "cancelled"


async def test_worker_requires_calendar_and_catches_missing_hour(session, monkeypatch):
    from app.tech_analysis import outcome_worker
    _, _ = await seed(session)
    class Client:
        base_url = "http://unused"
        async def fetch_candles(self, *args, **kwargs):
            return [bar(1), bar(3, o=103, h=111, low=102, c=110)]
    async def schedule(*args):
        return [(T + timedelta(hours=1), T + timedelta(hours=4))]
    monkeypatch.setattr(outcome_worker, "schedule", schedule)
    await evaluate_pending(session, client=Client(), now=T + timedelta(hours=5))
    e = await session.scalar(select(ScenarioEvaluation).where(ScenarioEvaluation.state == "open"))
    assert e is not None and not e.complete
    assert e.cursor_at.replace(tzinfo=timezone.utc) == T + timedelta(hours=1)
    async def unavailable(*args):
        raise ValueError("calendar missing")
    monkeypatch.setattr(outcome_worker, "schedule", unavailable)
    await evaluate_pending(session, client=Client(), now=T + timedelta(days=9))
    assert e.state == "open" and not e.complete


def test_calendar_excludes_closed_hours_and_unknown_freshness():
    from app.market.scenario_calendar import expected_hours, market_context
    windows = [(T + timedelta(hours=1), T + timedelta(hours=3) - timedelta(seconds=1)), (T + timedelta(hours=5), T + timedelta(hours=6))]
    assert expected_hours(windows, T, T + timedelta(hours=5)) == {T + timedelta(hours=1), T + timedelta(hours=2)}
    assert market_context(windows, T + timedelta(hours=4))["session"] == "closed"
    assert market_context(windows, T + timedelta(hours=2))["session"] == "open"


async def test_malformed_snapshot_is_excluded_not_failed(session):
    _, analysis = await seed(session)
    new = TechAnalysis(user_id=analysis.user_id, batch_id=analysis.batch_id, ticker="BAD", status="success", is_future=False, finished_at=T, scenario_json='{"scenario_a": [1], "execution_plans": [], "confirmation_plans": []}')
    session.add(new)
    await session.flush()
    await capture(session, new)
    await session.commit()
    rows = (await session.scalars(select(ScenarioSnapshot).where(ScenarioSnapshot.analysis_id == new.id))).all()
    assert len(rows) == 3 and not any(row.eligible for row in rows)
