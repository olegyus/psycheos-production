"""
Pro Bot webhook handler.
Phase 1: minimal /start response to verify the pipeline works.
Phase 2: full menu, case management, tool launching.
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Update, Bot

from app.webhooks.common import upsert_chat_state
from app.models.bot_chat_state import BotChatState

logger = logging.getLogger(__name__)


async def handle_pro(
    update: Update,
    bot: Bot,
    db: AsyncSession,
    state: BotChatState | None,
    chat_id: int,
    user_id: int | None,
) -> None:
    """Handle incoming update for Pro bot."""

    # Handle /start command
    if update.message and update.message.text and update.message.text.startswith("/start"):
        await upsert_chat_state(
            db,
            bot_id="pro",
            chat_id=chat_id,
            state="main_menu",
            user_id=user_id,
            role="specialist",
        )
        await bot.send_message(
            chat_id=chat_id,
            text="PsycheOS Pro — скелет работает.\nФаза 1 пройдена.",
        )
        return

    # Default: echo current state
    current_state = state.state if state else "no_state"
    await bot.send_message(
        chat_id=chat_id,
        text=f"[Pro] Текущее состояние: {current_state}",
    )
