import argparse
import asyncio

from sqlalchemy import select

from app.auth import hash_password
from app.db.connection import SessionLocal, init_db
from app.db.models import User


async def main() -> None:
    parser = argparse.ArgumentParser(description="Create a user account")
    parser.add_argument("username")
    parser.add_argument("password")
    args = parser.parse_args()

    await init_db()
    async with SessionLocal() as session:
        existing = await session.scalar(
            select(User).where(User.username == args.username.strip())
        )
        if existing is not None:
            print("User already exists")
            return
        user = User(
            username=args.username.strip(),
            password_hash=hash_password(args.password),
        )
        session.add(user)
        await session.commit()
        print(f"User {user.username} created")


if __name__ == "__main__":
    asyncio.run(main())
