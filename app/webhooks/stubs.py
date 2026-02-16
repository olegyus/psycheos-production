"""
Stub webhook handlers for Screen, Interpretator, Conceptualizator, Simulator.
Phase 1: minimal /start response to verify pipeline.
Phase 4: full logic migration from polling scripts.
"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Update, Bot

from app.webhooks.common import upsert_chat_state
from app.models.bot_chat_state import BotChatState

logger = logging.getLogger(__name__)


async def handle_screen(
    update: Update, bot: Bot, db: AsyncSession,
    state: BotChatState | None, chat_id: int, user_id: int | None,
) -> None:
    if update.message and update.message.text and update.message.text.startswith("/start"):
        await upsert_chat_state(db, bot_id="screen", chat_id=chat_id, state="idle", user_id=user_id, role="client")
        await bot.send_message(chat_id=chat_id, text="PsycheOS Screen — скелет работает.")
        return
    await bot.send_message(chat_id=chat_id, text="[Screen] Ожидание приглашения от специалиста.")


async def handle_interpretator(
    update: Update, bot: Bot, db: AsyncSession,
    state: BotChatState | None, chat_id: int, user_id: int | None,
) -> None:
    if update.message and update.message.text and update.message.text.startswith("/start"):
        await upsert_chat_state(db, bot_id="interpretator", chat_id=chat_id, state="idle", user_id=user_id, role="specialist")
        await bot.send_message(chat_id=chat_id, text="PsycheOS Interpretator — скелет работает.")
        return
    await bot.send_message(chat_id=chat_id, text="[Interpretator] Запуск через Pro.")


async def handle_conceptualizator(
    update: Update, bot: Bot, db: AsyncSession,
    state: BotChatState | None, chat_id: int, user_id: int | None,
) -> None:
    if update.message and update.message.text and update.message.text.startswith("/start"):
        await upsert_chat_state(db, bot_id="conceptualizator", chat_id=chat_id, state="idle", user_id=user_id, role="specialist")
        await bot.send_message(chat_id=chat_id, text="PsycheOS Conceptualizator — скелет работает.")
        return
    await bot.send_message(chat_id=chat_id, text="[Conceptualizator] Запуск через Pro.")


async def handle_simulator(
    update: Update, bot: Bot, db: AsyncSession,
    state: BotChatState | None, chat_id: int, user_id: int | None,
) -> None:
    if update.message and update.message.text and update.message.text.startswith("/start"):
        await upsert_chat_state(db, bot_id="simulator", chat_id=chat_id, state="idle", user_id=user_id, role="specialist")
        await bot.send_message(chat_id=chat_id, text="PsycheOS Simulator — скелет работает.")
        return
    await bot.send_message(chat_id=chat_id, text="[Simulator] Запуск через Pro.")
