import json
import math
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.db.models import ScenarioEvaluation, ScenarioSnapshot, ScenarioSubscription, ScenarioWatch, TechAnalysis

RULES_VERSION = "1"
TERMINAL = frozenset({"target", "stop", "expired_pending", "expired_open", "ambiguous", "cancelled"})
LABELS = {
    "zone": "Вход в диапазон",
    "pending": "Ожидание входа", "open": "Модельный вход", "confirmed": "Подтверждение входа",
    "target": "Первая цель", "stop": "Стоп", "expired_pending": "Истёк до входа",
    "expired_open": "Истёк после входа", "ambiguous": "Неоднозначно",
    "cancelled": "Отменён до входа", "unsupported": "Нужна ручная проверка",
}


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value > 0 else None


def text_numbers(value):
    if not isinstance(value, str):
        return []
    return [float(item.replace(",", ".")) for item in re.findall(r"\d+(?:[.,]\d+)?", value)]


def validate_plan(raw, scenario, confirmation=None):
    if not isinstance(raw, dict):
        return {}, "Нет машинного плана исполнения"
    plan = dict(raw)
    direction = plan.get("direction")
    if direction not in ("buy", "sell"):
        return {}, "WAIT или неизвестное направление"
    if plan.get("kind") not in ("touch", "close_above", "close_below"):
        return {}, "Условие требует ручной проверки"
    for key in ("entry", "stop", "target"):
        plan[key] = number(plan.get(key))
        if plan[key] is None:
            return {}, "Нет однозначных числовых уровней"
    if plan["kind"] != "touch" and number(plan.get("trigger")) is None:
        return {}, "Нет уровня подтверждения"
    if plan["kind"] == "touch" and confirmation:
        return {}, "Касание не заменяет текстовый план подтверждения"
    sign = 1 if direction == "buy" else -1
    if sign * (plan["entry"] - plan["stop"]) <= 0 or sign * (plan["target"] - plan["entry"]) <= 0:
        return {}, "Противоречивые уровни"
    for text_key, key in (("entry", "entry"), ("stop", "stop"), ("targets", "target")):
        values = text_numbers(scenario.get(text_key))
        if not values or not any(math.isclose(plan[key], item, rel_tol=1e-8) for item in values):
            return {}, "Машинные уровни расходятся с текстом сценария"
        if text_key == "targets" and not math.isclose(plan[key], values[0], rel_tol=1e-8):
            return {}, "Первая цель расходится с текстом"
    days = plan.get("valid_days")
    if days is not None and (isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 30):
        return {}, "Неподдерживаемый срок"
    cancellation = plan.get("cancel")
    if cancellation is not None:
        if not isinstance(cancellation, dict) or cancellation.get("kind") not in ("close_above", "close_below") or number(cancellation.get("level")) is None:
            return {}, "Неподдерживаемая отмена"
    zone = plan.get("zone")
    if zone is not None:
        if not isinstance(zone, list) or len(zone) != 2 or any(number(item) is None for item in zone) or zone[0] > zone[1] or not zone[0] <= plan["entry"] <= zone[1]:
            return {}, "Неверный диапазон входа"
    return plan, ""


async def watch_snapshots(session, analysis_id, user_id):
    if user_id is None:
        return
    ids = (await session.scalars(select(ScenarioSnapshot.id).where(ScenarioSnapshot.analysis_id == analysis_id))).all()
    for snapshot_id in ids:
        exists = await session.scalar(select(ScenarioWatch.id).where(ScenarioWatch.user_id == user_id, ScenarioWatch.snapshot_id == snapshot_id))
        if exists is None:
            session.add(ScenarioWatch(user_id=user_id, snapshot_id=snapshot_id))


async def capture(session, analysis):
    if analysis.batch_id is None or analysis.finished_at is None:
        return
    existing = await session.scalar(select(ScenarioSnapshot.id).where(ScenarioSnapshot.analysis_id == analysis.id))
    if existing is None:
        try:
            raw = json.loads(analysis.scenario_json or "{}")
            json.dumps(raw, allow_nan=False)
        except (ValueError, TypeError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        for key in "abc":
            scenario = raw.get(f"scenario_{key}")
            scenario = scenario if isinstance(scenario, dict) else {}
            plans = raw.get("execution_plans") or {}
            confirmations = raw.get("confirmation_plans")
            confirmation = confirmations.get(key) if isinstance(confirmations, dict) else None
            plan, reason = validate_plan(plans.get(key) if isinstance(plans, dict) else None, scenario, confirmation)
            published = utc(analysis.finished_at)
            snapshot = ScenarioSnapshot(
                analysis_id=analysis.id, scenario_key=key, ticker=analysis.ticker,
                is_future=analysis.is_future, published_at=published,
                expires_at=published + timedelta(days=plan.get("valid_days") or 7),
                model=analysis.model or "", provider=analysis.provider or "", rules_version=RULES_VERSION,
                raw=raw, plan=plan, eligible=not reason, reason=reason,
            )
            session.add(snapshot)
            await session.flush()
            session.add(ScenarioEvaluation(snapshot_id=snapshot.id, state="pending" if not reason else "unsupported"))
    await session.flush()
    await watch_snapshots(session, analysis.id, analysis.user_id)


def transition(evaluation, plan, bars, published, expires, now, coverage=True):
    events = []
    if evaluation.state in TERMINAL or evaluation.state == "unsupported":
        return events
    sign = 1 if plan["direction"] == "buy" else -1

    def emit(kind, at, price=None):
        events.append((kind, at, {"price": price}))

    def finish(kind, at, price=None):
        evaluation.state = kind
        evaluation.exit_at = at
        evaluation.exit_price = price
        if price is not None and evaluation.entry_price is not None:
            risk = abs(evaluation.entry_price - plan["stop"])
            evaluation.result_r = sign * (price - evaluation.entry_price) / risk if risk else None
        emit(kind, at, price)

    last = None
    for bar in bars:
        start, end = utc(bar["begin_at"]), utc(bar["end_at"])
        if start <= published or end >= now or end > expires:
            continue
        last = bar
        if evaluation.cursor_at is not None and start <= utc(evaluation.cursor_at):
            continue
        o, h, low, c = (bar[key] for key in ("open", "high", "low", "close"))
        if any(number(value) is None for value in (o, h, low, c)) or not low <= min(o, c) <= max(o, c) <= h:
            evaluation.detail = "Некорректная свеча; обработка остановлена"
            coverage = False
            break
        evaluation.cursor_at = start
        if evaluation.state == "pending":
            zone = plan.get("zone")
            if zone and low <= zone[1] and h >= zone[0]:
                emit("zone", end)
            cancel = plan.get("cancel")
            cancelled = cancel and ((cancel["kind"] == "close_above" and c > cancel["level"]) or (cancel["kind"] == "close_below" and c < cancel["level"]))
            if cancelled:
                may_enter = evaluation.confirmed or (plan["kind"] == "touch" and (low <= plan["entry"] <= h or sign * (o - plan["entry"]) < 0))
                finish("ambiguous" if may_enter else "cancelled", end)
                if may_enter:
                    evaluation.detail = "Порядок входа и отмены внутри свечи неизвестен"
                break
            if plan["kind"] == "touch":
                if sign * (o - plan["entry"]) < 0:
                    finish("ambiguous", start)
                    evaluation.detail = "Открытие за ценой лимитного входа"
                    break
                enter = low <= plan["entry"] <= h
                price = plan["entry"]
            else:
                enter = evaluation.confirmed
                price = o
                if not enter:
                    triggered = (plan["kind"] == "close_above" and c > plan["trigger"]) or (plan["kind"] == "close_below" and c < plan["trigger"])
                    if triggered:
                        evaluation.confirmed = True
                        emit("confirmed", end)
                    continue
            if not enter:
                continue
            if sign * (price - plan["stop"]) <= 0 or sign * (plan["target"] - price) <= 0:
                finish("ambiguous", start)
                evaluation.detail = "Открытие за стопом или целью"
                break
            evaluation.entry_price, evaluation.entry_at = price, start if plan["kind"] != "touch" else end
            evaluation.state = "open"
            emit("open", evaluation.entry_at, price)
            if plan["kind"] == "touch" and o != price and (low <= plan["stop"] <= h or low <= plan["target"] <= h):
                finish("ambiguous", end)
                evaluation.detail = "Порядок входа и выхода внутри свечи неизвестен"
                break
        if evaluation.state == "open":
            stop_hit = low <= plan["stop"] if sign > 0 else h >= plan["stop"]
            target_hit = h >= plan["target"] if sign > 0 else low <= plan["target"]
            if sign * (o - plan["stop"]) <= 0:
                finish("stop", start, o)
            elif sign * (o - plan["target"]) >= 0:
                finish("target", start, plan["target"])
            elif stop_hit and target_hit:
                finish("ambiguous", end)
                evaluation.detail = "Цель и стоп в одной свече"
            elif stop_hit:
                finish("stop", end, plan["stop"])
            elif target_hit:
                finish("target", end, plan["target"])
            if evaluation.state in TERMINAL:
                break
    evaluation.complete = coverage
    if now >= expires and evaluation.state not in TERMINAL and coverage:
        if evaluation.state == "pending":
            finish("expired_pending", expires)
        elif last is not None:
            finish("expired_open", expires, last["close"])
        else:
            evaluation.complete = False
            evaluation.detail = "Нет цены для закрытия по сроку"
    return events


async def owned(session, snapshot_id, user_id):
    return await session.scalar(select(ScenarioSnapshot).join(ScenarioWatch).where(ScenarioSnapshot.id == snapshot_id, ScenarioWatch.user_id == user_id))


def freshness(snapshot, evaluation, now=None):
    now = now or datetime.now(timezone.utc)
    age = max(0, (now - utc(snapshot.published_at)).total_seconds())
    checked = utc(evaluation.checked_at) if evaluation.checked_at else None
    context = evaluation.market_context or {}
    session = context.get("session", "unknown")
    expected = datetime.fromisoformat(context["expected_last"]) if context.get("expected_last") else None
    delayed = checked is None or (now - checked).total_seconds() > 600
    terminal = evaluation.state in TERMINAL
    market_fresh = not delayed and evaluation.complete and expected is not None and evaluation.cursor_at is not None and utc(evaluation.cursor_at) >= expected
    notes = {"unknown": "Расписание торгов не подтверждено; свежесть рыночных данных не гарантируется",
             "closed": "Сейчас вне торговой сессии MOEX", "open": "Сейчас торговая сессия MOEX"}
    return {
        "age_hours": round(age / 3600, 1), "expired": now >= utc(snapshot.expires_at),
        "terminal": terminal, "checked_at": checked,
        "observation_delayed": delayed and not terminal,
        "data_complete": evaluation.complete, "market_session": session if not delayed else "unknown",
        "market_data_fresh": market_fresh and session != "unknown" and not terminal,
        "market_note": notes.get(session if not delayed else "unknown", notes["unknown"]),
        "last_candle_at": evaluation.cursor_at,
    }


def outcome_query(user_id, ticker=None, state=None, model=None, since=None, until=None, hide_finished=False):
    query = select(ScenarioSnapshot, ScenarioEvaluation).join(ScenarioEvaluation).join(ScenarioWatch).where(ScenarioWatch.user_id == user_id)
    if ticker:
        query = query.where(ScenarioSnapshot.ticker == ticker.upper())
    if state:
        query = query.where(ScenarioEvaluation.state == state)
    if model:
        query = query.where(ScenarioSnapshot.model == model)
    if since:
        query = query.where(ScenarioSnapshot.published_at >= since)
    if until:
        query = query.where(ScenarioSnapshot.published_at <= until)
    if hide_finished:
        query = query.where(ScenarioEvaluation.state.not_in(TERMINAL), ScenarioSnapshot.expires_at > datetime.now(timezone.utc))
    return query


async def list_outcomes(session, user_id, ticker=None, state=None, model=None, since=None, until=None, offset=0, limit=50, hide_finished=False):
    query = outcome_query(user_id, ticker, state, model, since, until, hide_finished)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = (await session.execute(query.order_by(ScenarioSnapshot.id.desc()).offset(offset).limit(limit))).all()
    subscriptions = (await session.scalars(select(ScenarioSubscription).where(ScenarioSubscription.user_id == user_id, ScenarioSubscription.snapshot_id.in_([snapshot.id for snapshot, evaluation in rows])))).all()
    subscribed = {row.snapshot_id: row for row in subscriptions}
    items = []
    for snapshot, evaluation in rows:
        subscription = subscribed.get(snapshot.id)
        items.append({
            "id": snapshot.id, "analysis_id": snapshot.analysis_id, "ticker": snapshot.ticker,
            "scenario": snapshot.scenario_key.upper(), "model": snapshot.model, "published_at": snapshot.published_at,
            "expires_at": snapshot.expires_at, "plan": snapshot.plan, "eligible": snapshot.eligible,
            "reason": snapshot.reason, "rules_version": snapshot.rules_version,
            "state": evaluation.state, "label": LABELS.get(evaluation.state, evaluation.state),
            "entry_price": evaluation.entry_price, "exit_price": evaluation.exit_price,
            "result_r": evaluation.result_r, "detail": evaluation.detail,
            "freshness": freshness(snapshot, evaluation),
            "subscribed": bool(subscription and subscription.active),
            "subscription_channels": subscription.channels if subscription and subscription.active else [],
            "subscription_kinds": subscription.kinds if subscription and subscription.active else [],
        })
    return {"items": items, "total": total or 0, "offset": offset, "limit": limit}


async def summary(session, user_id, **filters):
    source = outcome_query(user_id, **filters).subquery()
    counts = dict((await session.execute(select(source.c.state, func.count()).group_by(source.c.state))).all())
    reasons = dict((await session.execute(select(source.c.reason, func.count()).where(source.c.reason != "").group_by(source.c.reason))).all())
    finished = select(source).where(source.c.eligible.is_(True), source.c.complete.is_(True), source.c.state.in_(TERMINAL - {"ambiguous"})).subquery()
    denominator = await session.scalar(select(func.count()).select_from(finished)) or 0
    entered = select(finished).where(finished.c.entry_price.is_not(None)).subquery()
    n = await session.scalar(select(func.count()).select_from(entered)) or 0
    targets = await session.scalar(select(func.count()).select_from(entered).where(entered.c.state == "target")) or 0
    count, mean = (await session.execute(select(func.count(entered.c.result_r), func.avg(entered.c.result_r)))).one()
    return {"total": sum(counts.values()), "states": counts, "excluded_reasons": reasons,
            "completed_denominator": denominator, "entered": n,
            "entry_rate": n / denominator if denominator else None,
            "target_rate": targets / n if n else None,
            "result_count": count, "mean_r": mean,
            "rules_version": RULES_VERSION,
            "note": "Модельные сценарии кандидатов; A/B/C зависимы. Комиссии и проскальзывание не учтены."}


async def attach_outcomes(session, items, user_id):
    ids = [item.get("analysis_id") for item in items]
    analyses = (await session.scalars(select(TechAnalysis).where(TechAnalysis.id.in_(ids)))).all()
    sources = {row.id: row.outcome_source_id or row.id for row in analyses}
    rows = (await session.execute(select(ScenarioSnapshot, ScenarioEvaluation).join(ScenarioEvaluation).join(ScenarioWatch).where(ScenarioWatch.user_id == user_id, ScenarioSnapshot.analysis_id.in_(sources.values())))).all()
    mapping = {(snapshot.analysis_id, snapshot.scenario_key): (snapshot, evaluation) for snapshot, evaluation in rows}
    for item in items:
        key = (sources.get(item.get("analysis_id")), (item.get("strategy") or "")[-1:])
        pair = mapping.get(key)
        if pair:
            snapshot, evaluation = pair
            item["observation"] = {"id": snapshot.id, "state": evaluation.state, "label": LABELS.get(evaluation.state, evaluation.state), "freshness": freshness(snapshot, evaluation)}
        else:
            item["observation"] = None
