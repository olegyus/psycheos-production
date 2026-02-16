"""
Webhook common layer — shared logic for all bot webhooks.

Responsibilities:
1. Verify Telegram secret token (403 if invalid)
2. Deduplicate updates (skip if already processed)
3. Load / save bot_chat_state from DB
"""
import logging
from fastapi import Request, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert
from telegram import Update, Bot

from app.models.bot_chat_state import BotChatState
from app.models.telegram_dedup import TelegramUpdateDedup

logger = logging.getLogger(__name__)


def verify_secret(request: Request, expected_secret: str) -> None:
    """
    Check X-Telegram-Bot-Api-Secret-Token header.
    Raises 403 if missing or mismatched.
    """
    token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if token != expected_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")


async def is_duplicate_update(
    db: AsyncSession, bot_id: str, update_id: int, chat_id: int
) -> bool:
    """
    Try to insert update_id into dedup table.
    Returns True if duplicate (already exists), False if new.
    """
    stmt = (
        pg_insert(TelegramUpdateDedup)
        .values(bot_id=bot_id, update_id=update_id, chat_id=chat_id)
        .on_conflict_do_nothing(index_elements=["bot_id", "update_id"])
    )
    result = await db.execute(stmt)
    await db.flush()

    # rowcount == 0 means conflict → duplicate
    return result.rowcount == 0


async def load_chat_state(
    db: AsyncSession, bot_id: str, chat_id: int
) -> BotChatState | None:
    """Load current FSM state for (bot, chat) pair."""
    stmt = select(BotChatState).where(
        BotChatState.bot_id == bot_id,
        BotChatState.chat_id == chat_id,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def upsert_chat_state(
    db: AsyncSession,
    bot_id: str,
    chat_id: int,
    state: str,
    state_payload: dict | None = None,
    user_id: int | None = None,
    role: str = "specialist",
    context_id=None,
) -> BotChatState:
    """
    Create or update FSM state for (bot, chat).
    Uses INSERT ... ON CONFLICT UPDATE for atomicity.
    """
    payload = state_payload or {}

    stmt = pg_insert(BotChatState).values(
        bot_id=bot_id,
        chat_id=chat_id,
        user_id=user_id,
        role=role,
        state=state,
        state_payload=payload,
        context_id=context_id,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["bot_id", "chat_id"],
        set_={
            "state": state,
            "state_payload": payload,
            "user_id": user_id or stmt.excluded.user_id,
            "role": role,
            "context_id": context_id,
            "updated_at": text("now()"),
        },
    )
    await db.execute(stmt)
    await db.flush()

    # Return the current state
    return await load_chat_state(db, bot_id, chat_id)


def extract_chat_id(update: Update) -> int | None:
    """Extract chat_id from any type of Telegram update."""
    if update.message:
        return update.message.chat_id
    if update.callback_query and update.callback_query.message:
        return update.callback_query.message.chat_id
    if update.edited_message:
        return update.edited_message.chat_id
    return None


def extract_user_id(update: Update) -> int | None:
    """Extract user telegram_id from any type of Telegram update."""
    if update.message:
        return update.message.from_user.id if update.message.from_user else None
    if update.callback_query:
        return update.callback_query.from_user.id if update.callback_query.from_user else None
    if update.edited_message:
        return update.edited_message.from_user.id if update.edited_message.from_user else None
    return None
