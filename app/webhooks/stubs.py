"""
Webhook handlers for Screen, Conceptualizator, Simulator (stubs until Phase 4).
Interpretator has been migrated to app/webhooks/interpretator.py (Phase 4).

Phase 3: /start TOKEN → verify link → save context_id + run_id to FSM.
Phase 4: full tool logic (AI calls, question flow, etc.) replaces placeholders.

Entry flow for all tool bots:
  /start {jti}  → verify_link() → FSM state="active", save context_id + run_id
  /start        → reject (no token)
  any message   → if active: placeholder; else: "launch from Pro"
"""
import logging

from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Update, Bot

from app.webhooks.common import upsert_chat_state
from app.models.bot_chat_state import BotChatState
from app.services.links import verify_link, LinkVerifyError

logger = logging.getLogger(__name__)


# ── Shared verify entry flow ──────────────────────────────────────────────────

async def _handle_tool_start(
    bot: Bot,
    db: AsyncSession,
    chat_id: int,
    user_id: int,
    bot_id: str,
    raw_token: str,
) -> None:
    """
    Verify the link token and initialise FSM state for the tool session.
    Called when a tool bot receives /start {token}.
    """
    try:
        token = await verify_link(
            db,
            raw_token=raw_token,
            service_id=bot_id,
            subject_id=user_id,
        )
    except LinkVerifyError as e:
        logger.info(f"[{bot_id}] verify_link failed for user {user_id}: {e}")
        await bot.send_message(
            chat_id=chat_id,
            text=f"❌ Доступ закрыт: {e}\n\n"
                 f"Вернитесь в Pro и запросите новую ссылку.",
        )
        return

    await upsert_chat_state(
        db,
        bot_id=bot_id,
        chat_id=chat_id,
        state="active",
        state_payload={"run_id": str(token.run_id)},
        user_id=user_id,
        role=token.role,
        context_id=token.context_id,
    )
    logger.info(
        f"[{bot_id}] Session started: user={user_id} "
        f"context={token.context_id} run_id={token.run_id}"
    )

    # Phase 4: replace with actual tool welcome message / first step
    await bot.send_message(
        chat_id=chat_id,
        text="✅ Сессия открыта.\n\n_Логика инструмента — Фаза 4._",
        parse_mode="Markdown",
    )


async def _handle_tool_message(
    bot: Bot,
    chat_id: int,
    bot_id: str,
    state: BotChatState | None,
) -> None:
    """Handle non-/start messages for tool bots (placeholder until Phase 4)."""
    if state and state.state == "active":
        # Phase 4: route to actual tool FSM
        await bot.send_message(
            chat_id=chat_id,
            text="_Обработка сообщений — Фаза 4._",
            parse_mode="Markdown",
        )
    else:
        await bot.send_message(
            chat_id=chat_id,
            text="Для запуска используйте ссылку из Pro.",
        )


# ── Bot handlers ──────────────────────────────────────────────────────────────

async def handle_screen(
    update: Update, bot: Bot, db: AsyncSession,
    state: BotChatState | None, chat_id: int, user_id: int | None,
) -> None:
    if not (update.message and update.message.text):
        return
    text = update.message.text.strip()

    if text.startswith("/start"):
        parts = text.split(" ", 1)
        if len(parts) == 2 and parts[1]:
            await _handle_tool_start(bot, db, chat_id, user_id, "screen", parts[1])
        else:
            await bot.send_message(
                chat_id=chat_id,
                text="❌ Доступ ограничен.\n\nОжидайте ссылку от специалиста.",
            )
        return

    await _handle_tool_message(bot, chat_id, "screen", state)


async def handle_conceptualizator(
    update: Update, bot: Bot, db: AsyncSession,
    state: BotChatState | None, chat_id: int, user_id: int | None,
) -> None:
    if not (update.message and update.message.text):
        return
    text = update.message.text.strip()

    if text.startswith("/start"):
        parts = text.split(" ", 1)
        if len(parts) == 2 and parts[1]:
            await _handle_tool_start(bot, db, chat_id, user_id, "conceptualizator", parts[1])
        else:
            await bot.send_message(
                chat_id=chat_id,
                text="❌ Доступ ограничен.\n\nЗапустите инструмент через Pro.",
            )
        return

    await _handle_tool_message(bot, chat_id, "conceptualizator", state)


async def handle_simulator(
    update: Update, bot: Bot, db: AsyncSession,
    state: BotChatState | None, chat_id: int, user_id: int | None,
) -> None:
    if not (update.message and update.message.text):
        return
    text = update.message.text.strip()

    if text.startswith("/start"):
        parts = text.split(" ", 1)
        if len(parts) == 2 and parts[1]:
            await _handle_tool_start(bot, db, chat_id, user_id, "simulator", parts[1])
        else:
            await bot.send_message(
                chat_id=chat_id,
                text="❌ Доступ ограничен.\n\nЗапустите инструмент через Pro.",
            )
        return

    await _handle_tool_message(bot, chat_id, "simulator", state)
