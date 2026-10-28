from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.models import ScenarioCandle, ScenarioEvaluation, ScenarioEvent, ScenarioSnapshot, TechAnalysis
from app.brokers.t_invest import connection_for
from app.market.moex import MOEXClient
from app.market.scenario_calendar import CalendarAuthorizationError, expected_hours, market_context, schedule
from app.market import t_invest_calendar
from app.tech_analysis.outcomes import TERMINAL, transition, utc


async def evaluate_pending(session, limit=100, client=None, now=None):
    now = now or datetime.now(timezone.utc)
    client = client or MOEXClient()
    query = select(ScenarioEvaluation, ScenarioSnapshot).join(ScenarioSnapshot).where(
        ScenarioSnapshot.eligible.is_(True), ScenarioEvaluation.state.not_in(TERMINAL),
    ).order_by(ScenarioEvaluation.checked_at.asc().nullsfirst(), ScenarioSnapshot.id).limit(limit).with_for_update(of=ScenarioEvaluation, skip_locked=True)
    rows = (await session.execute(query)).all()
    done = 0
    candle_cache = {}
    calendar_cache = {}
    for evaluation, snapshot in rows:
        try:
            start = utc(snapshot.published_at)
            stop = min(now, utc(snapshot.expires_at))
            zone = timezone(timedelta(hours=3))
            candle_key = (snapshot.ticker, snapshot.is_future, start.astimezone(zone).date(), stop.astimezone(zone).date())
            if candle_key not in candle_cache:
                candle_cache[candle_key] = sorted(await client.fetch_candles(snapshot.ticker, candle_key[2], candle_key[3], interval=60, security_type="futures" if snapshot.is_future else "stock", include_time=True), key=lambda bar: bar["begin_at"])
            bars = candle_cache[candle_key]
            usable = []
            complete = True
            windows = None
            if hasattr(client, "base_url"):
                try:
                    owner = await session.scalar(select(TechAnalysis.user_id).where(TechAnalysis.id == snapshot.analysis_id))
                    connection = await connection_for(session, owner) if owner is not None else None
                    calendar_key = (connection.id if connection else None, snapshot.ticker, snapshot.is_future, start.astimezone(zone).date(), stop.astimezone(zone).date())
                    if calendar_key not in calendar_cache:
                        if connection:
                            calendar_cache[calendar_key] = await t_invest_calendar.schedule(connection, snapshot.ticker, snapshot.is_future, start.astimezone(zone), stop.astimezone(zone), now)
                        else:
                            calendar_cache[calendar_key] = (await schedule(client.base_url, snapshot.ticker, snapshot.is_future, start.astimezone(zone), stop.astimezone(zone)), {"provider": "moex"})
                    windows, calendar_metadata = calendar_cache[calendar_key]
                    evaluation.market_context = market_context(windows, now) if now.date() == stop.date() else {"session": "unknown"}
                    evaluation.market_context = {**evaluation.market_context, "calendar": calendar_metadata}
                except Exception as exc:
                    evaluation.market_context = {"session": "unknown"}
                    complete = False
                    evaluation.detail = str(exc) if isinstance(exc, (CalendarAuthorizationError, t_invest_calendar.CalendarDataError)) else "Календарь не подтверждён; полнота окна неизвестна"
                    evaluation.checked_at = now
                    evaluation.complete = False
                    continue
            else:
                evaluation.market_context = {"session": "unknown"}
            expected = expected_hours(windows, start, stop) if windows is not None else None
            actual = {bar["begin_at"] for bar in bars if bar["end_at"] < now}
            missing = sorted(expected - actual) if expected is not None else []
            if missing:
                complete = False
                evaluation.detail = "В расписании MOEX есть часы без свечей; исход пока не определён"
            elif complete:
                evaluation.detail = ""
            for bar in bars:
                if bar["begin_at"] <= start or bar["end_at"] >= now or bar["end_at"] > stop:
                    continue
                if missing and bar["begin_at"] >= missing[0]:
                    break
                usable.append(bar)
            if not usable and (expected is None or expected):
                complete = False
            for bar in usable:
                values = {
                    "ticker": snapshot.ticker, "is_future": snapshot.is_future,
                    "begin_at": bar["begin_at"], "end_at": bar["end_at"],
                    "data": {key: bar[key] for key in ("open", "high", "low", "close", "volume")},
                    "source": "MOEX ISS 60", "fetched_at": now,
                }
                if session.bind.dialect.name == "postgresql":
                    from sqlalchemy.dialects.postgresql import insert
                else:
                    from sqlalchemy.dialects.sqlite import insert
                await session.execute(insert(ScenarioCandle).values(**values).on_conflict_do_nothing(index_elements=["ticker", "is_future", "begin_at"]))
            cached = (await session.scalars(select(ScenarioCandle).where(
                ScenarioCandle.ticker == snapshot.ticker, ScenarioCandle.is_future == snapshot.is_future,
                ScenarioCandle.begin_at > start, ScenarioCandle.end_at <= stop,
            ).order_by(ScenarioCandle.begin_at))).all()
            bar_limit = usable[-1]["begin_at"] if usable else start
            frozen_bars = [{**bar.data, "begin_at": utc(bar.begin_at), "end_at": utc(bar.end_at)} for bar in cached if utc(bar.begin_at) <= bar_limit]
            events = transition(evaluation, snapshot.plan, frozen_bars, start, utc(snapshot.expires_at), now, complete)
            for kind, at, data in events:
                exists = await session.scalar(select(ScenarioEvent.id).where(ScenarioEvent.snapshot_id == snapshot.id, ScenarioEvent.kind == kind))
                if exists is None:
                    session.add(ScenarioEvent(snapshot_id=snapshot.id, kind=kind, occurred_at=at, recorded_at=now, data={**data, "calendar": (evaluation.market_context or {}).get("calendar")}))
            evaluation.checked_at = now
            done += 1
        except Exception as exc:
            evaluation.complete = False
            evaluation.detail = f"Данные не обновлены: {type(exc).__name__}"
            evaluation.checked_at = now
    await session.commit()
    return done
