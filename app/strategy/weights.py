from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EvidenceItem, FactorWeight, Strategy, UserFeedback

DEFAULT_FACTORS = {"news": 1.0, "graph": 1.0, "counter_penalty": 1.0}
MIN_FACTOR = 0.5
MAX_FACTOR = 1.5
SHARE_DELTA = 0.1
ADJUST_STEP = 0.9


async def get_latest(session: AsyncSession) -> tuple[str | None, dict]:
    row = await session.scalar(
        select(FactorWeight).order_by(FactorWeight.id.desc()).limit(1)
    )
    if row is None:
        return None, dict(DEFAULT_FACTORS)
    return row.version, dict(row.factors)


async def create_version(
    session: AsyncSession, factors: dict, description: str = ""
) -> str:
    latest, _ = await get_latest(session)
    number = 1 if latest is None else int(latest.lstrip("w")) + 1
    version = f"w{number}"
    session.add(
        FactorWeight(
            version=version,
            factors=dict(factors),
            description=description,
        )
    )
    await session.commit()
    return version


async def calibrate(session: AsyncSession, description: str = "") -> dict:
    rows = (
        await session.execute(
            select(UserFeedback, Strategy).join(
                Strategy, Strategy.id == UserFeedback.strategy_id
            )
        )
    ).all()
    buckets = {
        "worked": {"news": 0.0, "graph": 0.0, "n": 0},
        "failed": {"news": 0.0, "graph": 0.0, "n": 0},
    }
    for feedback, strategy in rows:
        if feedback.rating not in ("worked", "failed"):
            continue
        items = (
            await session.scalars(
                select(EvidenceItem).where(EvidenceItem.strategy_id == strategy.id)
            )
        ).all()
        news_w = sum(abs(i.weight) for i in items if i.kind == "news_fact")
        graph_w = sum(abs(i.weight) for i in items if i.kind == "graph_path")
        total = news_w + graph_w
        if total <= 0:
            continue
        bucket = buckets[feedback.rating]
        bucket["news"] += news_w / total
        bucket["graph"] += graph_w / total
        bucket["n"] += 1

    _, factors = await get_latest(session)
    changes: list[str] = []

    worked, failed = buckets["worked"], buckets["failed"]
    if worked["n"] > 0 and failed["n"] > 0:
        worked_graph = worked["graph"] / worked["n"]
        failed_graph = failed["graph"] / failed["n"]
        worked_news = worked["news"] / worked["n"]
        failed_news = failed["news"] / failed["n"]
        if failed_graph - worked_graph >= SHARE_DELTA and factors["graph"] > MIN_FACTOR:
            factors["graph"] = round(factors["graph"] * ADJUST_STEP, 3)
            changes.append(
                f"graph {failed_graph:.2f} vs {worked_graph:.2f} -> снижен до {factors['graph']}"
            )
        if worked_news - failed_news >= SHARE_DELTA and factors["news"] < MAX_FACTOR:
            factors["news"] = round(factors["news"] / ADJUST_STEP, 3)
            changes.append(
                f"news {worked_news:.2f} vs {failed_news:.2f} -> повышен до {factors['news']}"
            )

    version = await create_version(session, factors, description)
    return {
        "version": version,
        "factors": factors,
        "changes": changes,
        "worked_count": worked["n"],
        "failed_count": failed["n"],
    }
