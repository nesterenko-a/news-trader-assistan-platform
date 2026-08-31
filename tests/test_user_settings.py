from io import BytesIO

import pytest
from PIL import Image
from starlette.datastructures import UploadFile

from app.web import router as web_router


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (640, 320), "red").save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_store_avatar_converts_and_replaces_previous(tmp_path, monkeypatch):
    monkeypatch.setattr(web_router, "_AVATAR_DIR", tmp_path)
    old = tmp_path / "old.webp"
    old.write_bytes(b"old")
    upload = UploadFile(filename="avatar.png", file=BytesIO(_png_bytes()))

    stored = await web_router._store_avatar(upload, "avatars/old.webp")

    assert stored.startswith("avatars/")
    assert not old.exists()
    saved = tmp_path / stored.split("/", 1)[1]
    with Image.open(saved) as image:
        assert image.format == "WEBP"
        assert max(image.size) <= 256


@pytest.mark.asyncio
async def test_store_avatar_rejects_non_image(tmp_path, monkeypatch):
    monkeypatch.setattr(web_router, "_AVATAR_DIR", tmp_path)
    upload = UploadFile(filename="avatar.txt", file=BytesIO(b"not an image"))

    with pytest.raises(ValueError, match="изображение"):
        await web_router._store_avatar(upload, None)
