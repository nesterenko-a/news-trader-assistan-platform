from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from app.config import get_settings
from app.db.connection import SessionLocal
from app.db.models import Security
from app.news.service import load_security_news
from app.presentation.factories import TelegramMessageFactory
from app.presentation.view import build_strategy_view
from app.strategy.engine import generate_strategy

_telegram_factory = TelegramMessageFactory()


async def _build_reply(ticker: str) -> dict:
    settings = get_settings()
    async with SessionLocal() as session:
        security = await session.scalar(
            select(Security).where(Security.ticker == ticker)
        )
        if security is None:
            return {
                "found": False,
                "text": (
                    f"Бумага <b>{ticker}</b> не найдена.\n"
                    "Попробуйте тикер из списка: AFLT, SBER, LKOH, GAZP, NLMK, PLZL, YDEX и др."
                ),
            }
        result = await generate_strategy(session, ticker)
        news = await load_security_news(session, security.id, limit=10)

    view = build_strategy_view(
        security,
        result,
        web_url=f"{settings.app_url}/securities/{ticker}",
        news=news,
    )
    return {"found": True, "text": _telegram_factory.build(view)}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Я NewsTrader Assistant.\n"
        "Пришли тикер — например <b>AFLT</b> или <b>SBER</b> — и я подготовлю стратегию "
        "на основе новостного фона.\n"
        "Команды: /help",
        parse_mode="HTML",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Как пользоваться:\n"
        "- пришлите тикер (AFLT, SBER, LKOH, GAZP, NLMK, PLZL, YDEX и др.) — получите вердикт;\n"
        "- /start — приветствие;\n"
        "- /help — эта справка.\n"
        "Материалы носят информационный характер и не являются инвестиционной рекомендацией."
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ticker = (update.message.text or "").strip().upper()
    if not ticker:
        return

    reply = await _build_reply(ticker)
    await update.message.reply_text(
        reply["text"],
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
