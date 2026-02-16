"""
Webhook router factory — creates a standard webhook endpoint for any bot.

Pipeline for every incoming update:
1. Verify Telegram secret token → 403 if invalid
2. Parse Update object
3. Deduplicate by update_id → skip if already processed
4. Load FSM state from DB
5. Call bot-specific handler
6. Save FSM state to DB
7. Return 200 OK
"""
import logging
from typing import Callable, Awaitable

from fastapi import APIRouter, Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Update, Bot

from app.database import get_db
from app.webhooks.common import (
    verify_secret,
    is_duplicate_update,
    load_chat_state,
    upsert_chat_state,
    extract_chat_id,
    extract_user_id,
)

logger = logging.getLogger(__name__)

# Type for bot-specific handler function
BotHandler = Callable[
    [Update, Bot, AsyncSession, "BotChatState | None", int, int | None],
    Awaitable[None],
]


def create_webhook_router(
    bot_id: str,
    token: str,
    webhook_secret: str,
    handler: BotHandler,
) -> APIRouter:
    """
    Creates a FastAPI router with POST /webhook/{bot_id} endpoint.

    Args:
        bot_id: e.g. "pro", "screen"
        token: Telegram bot token
        webhook_secret: expected secret in header
        handler: async function(update, bot, db, state, chat_id, user_id) → None
    """
    router = APIRouter()
    bot = Bot(token=token)

    @router.post(f"/webhook/{bot_id}")
    async def webhook_endpoint(request: Request, db: AsyncSession = Depends(get_db)):
        # 1. Verify secret
        verify_secret(request, webhook_secret)

        # 2. Parse update
        data = await request.json()
        update = Update.de_json(data, bot)

        if not update:
            return {"ok": True}

        # 3. Extract identifiers
        chat_id = extract_chat_id(update)
        user_id = extract_user_id(update)

        if chat_id is None:
            logger.warning(f"[{bot_id}] No chat_id in update {update.update_id}")
            return {"ok": True}

        # 4. Deduplicate
        is_dup = await is_duplicate_update(db, bot_id, update.update_id, chat_id)
        if is_dup:
            logger.info(f"[{bot_id}] Duplicate update {update.update_id}, skipping")
            return {"ok": True}

        # 5. Load FSM state
        state = await load_chat_state(db, bot_id, chat_id)

        # 6. Call bot-specific handler
        try:
            await handler(update, bot, db, state, chat_id, user_id)
        except Exception as e:
            logger.exception(f"[{bot_id}] Error handling update {update.update_id}: {e}")
            # Still return 200 to Telegram — we don't want retries for app errors
            # Error is logged to Sentry

        # 7. Commit transaction
        await db.commit()

        return {"ok": True}

    return router
