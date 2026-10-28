import io
from importlib import import_module

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.brokers import appearance
from app.db.connection import Base, get_session
from app.db.models import User


@pytest_asyncio.fixture
async def store():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        users = [User(username="owner", password_hash="x"), User(username="other", password_hash="x")]
        session.add_all(users)
        await session.commit()
        yield session, users
    await engine.dispose()


def picture():
    output = io.BytesIO()
    Image.new("RGB", (1200, 700), "navy").save(output, format="PNG", pnginfo=None)
    return output.getvalue()


async def test_styles_are_independent_private_and_sanitized(store):
    session, (owner, other) = store
    row = await appearance.save_style(session, owner.id, "sandbox", color="#ABCDEF", image=picture())
    assert row.color == "#abcdef"
    with Image.open(io.BytesIO(row.image_data)) as image:
        assert image.format == "WEBP" and image.width <= 1024 and image.height <= 512
        assert not image.getexif()
    await appearance.save_style(session, owner.id, "production", color="#123456")
    assert (await appearance.style_for(session, owner.id, "sandbox")).image_data
    assert await appearance.style_for(session, other.id, "sandbox") is None
    await appearance.save_style(session, owner.id, "sandbox", color="#abcdef", reset=True)
    assert row.color == appearance.DEFAULT_COLOR and row.image_data is None
    assert (await appearance.style_for(session, owner.id, "production")).color == "#123456"


@pytest.mark.parametrize("content", [b"<svg onload='alert(1)'/>", b"not an image", b"x" * (appearance.MAX_IMAGE_BYTES + 1)], ids=["svg", "invalid", "oversized"])
async def test_invalid_images_do_not_change_saved_style(store, content):
    session, (owner, _) = store
    await appearance.save_style(session, owner.id, "sandbox", color="#123456", image=picture())
    with pytest.raises(ValueError):
        await appearance.save_style(session, owner.id, "sandbox", color="#abcdef", image=content)
    assert (await appearance.style_for(session, owner.id, "sandbox")).color == "#123456"
    with pytest.raises(ValueError):
        await appearance.save_style(session, owner.id, "sandbox", color="red; color: red")


async def test_web_image_owner_authentication_and_form_origin(store, monkeypatch):
    session, (owner, other) = store
    web = import_module("app.web.router")
    current = [owner]

    async def user(*args):
        return current[0]

    async def db():
        yield session

    monkeypatch.setattr(web, "_optional_user", user)
    app = FastAPI()
    app.include_router(web.router)
    app.dependency_overrides[get_session] = db
    await appearance.save_style(session, owner.id, "sandbox", color="#123456", image=picture())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        url = "/broker/t-invest/badge-image?environment=sandbox"
        response = await client.get(url)
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/webp"
        assert response.headers["cache-control"] == "private, no-store"
        current[0] = other
        assert (await client.get(url)).status_code == 404
        current[0] = None
        assert (await client.get(url)).status_code == 401
        current[0] = owner
        payload = {"environment": "sandbox", "color": "#abcdef"}
        assert (await client.post("/broker/t-invest/appearance", data=payload)).status_code == 403
        response = await client.post("/broker/t-invest/appearance", data=payload, headers={"Origin": "http://test"})
        assert response.status_code == 303 and "appearance=saved" in response.headers["location"]
        assert (await appearance.style_for(session, owner.id, "sandbox")).color == "#abcdef"
