from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User


async def promote_admin_users(session: AsyncSession, usernames: list[str]) -> int:
    if not usernames:
        return 0
    rows = (
        await session.scalars(select(User).where(User.username.in_(usernames)))
    ).all()
    promoted = 0
    for user in rows:
        if user.role != "admin":
            user.role = "admin"
            promoted += 1
    if promoted:
        await session.commit()
    return promoted
