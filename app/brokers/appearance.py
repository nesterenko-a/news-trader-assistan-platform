import asyncio
import re
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select

from app.brokers.t_invest import environment_base
from app.db.models import BrokerBadgeStyle

DEFAULT_COLOR = "#6897ff"
MAX_IMAGE_BYTES = 3 * 1024 * 1024


def encode_background(content):
    if len(content) > MAX_IMAGE_BYTES:
        raise ValueError("Картинка должна быть не больше 3 МБ")
    try:
        with Image.open(BytesIO(content)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"} or source.width * source.height > 20_000_000:
                raise ValueError("Выберите PNG, JPEG или WebP не больше 20 мегапикселей")
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((1024, 512))
            output = BytesIO()
            image.save(output, format="WEBP", quality=85)
            return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Не удалось прочитать картинку") from exc


async def style_for(session, user_id, environment):
    environment_base(environment)
    return await session.scalar(select(BrokerBadgeStyle).where(BrokerBadgeStyle.user_id == user_id, BrokerBadgeStyle.provider == "t_invest", BrokerBadgeStyle.environment == environment))


async def save_style(session, user_id, environment, *, color, image=None, remove_image=False, reset=False):
    environment_base(environment)
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise ValueError("Выберите цвет карточки")
    image_data = await asyncio.to_thread(encode_background, image) if image is not None and not reset else None
    row = await style_for(session, user_id, environment)
    if row is None:
        row = BrokerBadgeStyle(user_id=user_id, environment=environment)
        session.add(row)
    row.color = DEFAULT_COLOR if reset else color.lower()
    if reset or remove_image:
        row.image_data = None
    if image_data is not None:
        row.image_data = image_data
    await session.commit()
    return row
