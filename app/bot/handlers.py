from telegram import Update
from telegram.ext import ContextTypes

from app.config import get_settings
from app.db.connection import SessionLocal
from app.db.models import Security
from app.strategy.engine import generate_strategy

from sqlalchemy import select


async def _ticker_result(ticker: str) -> str:
    async with SessionLocal() as session:
        security = await session.scalar(
            select(Security).where(Security.ticker == ticker)
        )
        if security is None:
            return (
                f"Бумага <b>{ticker}</b> не найдена.\n"
                "Попробуйте тикер из списка: AFLT, SBER, LKOH, GAZP, NLMK, PLZL, YDEX и др."
            )
        result = await generate_strategy(session, ticker)

    strategy = result["strategy"]
    levels = strategy["levels"]
    settings = get_settings()

    lines = [
        f"<b>{security.ticker}</b> — {security.name} ({security.sector})",
        f"Вердикт: <b>{strategy['verdict']}</b>",
        f"Горизонт: {strategy['horizon']} · Уверенность: {strategy['confidence']} · Score: {strategy['net_score']}",
    ]
    if levels.get("entry"):
        lines.append(
            f"Вход: {levels['entry']} · TP: {levels['take_profit']} · SL: {levels['stop_loss']}"
        )
    if result["rationale_summary"]:
        lines.append(f"Обоснование: {result['rationale_summary']}")
    lines.append(f"Подробнее: {settings.app_url}/securities/{ticker}")
    return "\n".join(lines)


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
    text = (update.message.text or "").strip().upper()
    if not text:
        return
    reply = await _ticker_result(text)
    await update.message.reply_text(
        reply,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
