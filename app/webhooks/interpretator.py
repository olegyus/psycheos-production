"""
Webhook handler for Interpretator bot (Phase 4 → Phase 6 async).

State machine (bot_chat_state.state):
  active     — session opened via /start {jti}, awaiting first material
  intake     — enqueued INTAKE job; awaiting worker response
  completed  — interpretation sent; session closed

All Claude API calls have been moved to app/worker/handlers/interpretator.py.
The webhook now:
  1. Downloads + base64-encodes photos (no Claude).
  2. Appends material to accumulated_material.
  3. Persists state.
  4. Enqueues the appropriate worker job.
  5. Sends an immediate ack message.
"""
import base64
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot, Update

from app.models.bot_chat_state import BotChatState
from app.services.job_queue import enqueue
from app.services.links import LinkVerifyError, verify_link
from app.webhooks.common import upsert_chat_state

logger = logging.getLogger(__name__)

BOT_ID = "interpretator"


# ── Entry point ───────────────────────────────────────────────────────────────

async def handle_interpretator(
    update: Update, bot: Bot, db: AsyncSession,
    state: BotChatState | None, chat_id: int, user_id: int | None,
) -> None:
    msg = update.message
    if not msg:
        return

    if msg.text and msg.text.startswith("/start"):
        parts = msg.text.split(" ", 1)
        if len(parts) == 2 and parts[1].strip():
            await _start_session(bot, db, chat_id, user_id, parts[1].strip())
        else:
            await bot.send_message(
                chat_id=chat_id,
                text="❌ Запустите инструмент через бот Pro.",
            )
        return

    if msg.photo:
        await _handle_photo(bot, db, msg, state, chat_id, user_id)
        return

    if msg.text:
        await _handle_text(bot, db, msg.text, state, chat_id, user_id)


# ── Session start ─────────────────────────────────────────────────────────────

async def _start_session(
    bot: Bot, db: AsyncSession,
    chat_id: int, user_id: int | None, raw_token: str,
) -> None:
    """Verify link token and (re)initialise FSM. Resets any existing session."""
    try:
        token = await verify_link(
            db, raw_token=raw_token, service_id=BOT_ID, subject_id=user_id,
        )
    except LinkVerifyError as e:
        logger.info(f"[{BOT_ID}] verify_link failed user={user_id}: {e}")
        await bot.send_message(
            chat_id=chat_id,
            text=f"❌ Доступ закрыт: {e}\n\nВернитесь в Pro и запросите новую ссылку.",
        )
        return

    await upsert_chat_state(
        db,
        bot_id=BOT_ID,
        chat_id=chat_id,
        state="active",
        state_payload={
            "run_id": str(token.run_id),
            "mode": "STANDARD",
            "iteration_count": 0,
            "repair_attempts": 0,
            "material_type": "unknown",
            "completeness": "unknown",
            "accumulated_material": [],
            "clarifications_received": [],
        },
        user_id=user_id,
        role=token.role,
        context_id=token.context_id,
    )
    logger.info(
        f"[{BOT_ID}] Session started: user={user_id} "
        f"context={token.context_id} run_id={token.run_id}"
    )
    await bot.send_message(
        chat_id=chat_id,
        text=(
            "🧠 <b>PsycheOS Interpreter</b>\n\n"
            "Сессия открыта.\n\n"
            "Отправьте описание символического материала:\n"
            "• Сон\n"
            "• Рисунок (текстом или изображением)\n"
            "• Проективный образ"
        ),
        parse_mode="HTML",
    )


# ── Text handling ─────────────────────────────────────────────────────────────

async def _handle_text(
    bot: Bot, db: AsyncSession, text: str,
    state: BotChatState | None, chat_id: int, user_id: int | None,
) -> None:
    if state is None or state.state not in ("active", "intake"):
        if state and state.state == "completed":
            await bot.send_message(
                chat_id=chat_id,
                text="Сессия завершена. Запустите новую через бот Pro.",
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text="Для запуска используйте ссылку из бота Pro.",
            )
        return

    payload = dict(state.state_payload or {})
    payload.setdefault("accumulated_material", []).append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "content": text,
    })

    # Persist state with new material before enqueuing (data safety).
    await upsert_chat_state(
        db, bot_id=BOT_ID, chat_id=chat_id, state=state.state,
        state_payload=payload, user_id=user_id,
        role=state.role, context_id=state.context_id,
    )

    run_id = payload.get("run_id")
    await enqueue(
        db, "interp_intake", BOT_ID, chat_id,
        payload={"state_payload": payload, "role": state.role or "specialist"},
        user_id=user_id, context_id=state.context_id, run_id=run_id,
    )

    await bot.send_message(chat_id=chat_id, text="⏳ Анализирую материал...")


# ── Photo handling ────────────────────────────────────────────────────────────

async def _handle_photo(
    bot: Bot, db: AsyncSession, msg,
    state: BotChatState | None, chat_id: int, user_id: int | None,
) -> None:
    if state is None or state.state not in ("active", "intake"):
        await bot.send_message(
            chat_id=chat_id,
            text="Для запуска используйте ссылку из бота Pro.",
        )
        return

    await bot.send_message(chat_id=chat_id, text="📸 Изображение получено. Анализирую рисунок...")

    try:
        file_obj = await bot.get_file(msg.photo[-1].file_id)
        photo_bytes = await file_obj.download_as_bytearray()
        photo_b64 = base64.b64encode(bytes(photo_bytes)).decode()
    except Exception:
        logger.exception(f"[{BOT_ID}] Photo download error user={user_id}")
        await bot.send_message(
            chat_id=chat_id,
            text="❌ Ошибка при загрузке изображения. Попробуйте описать рисунок текстом.",
        )
        return

    payload = dict(state.state_payload or {})
    run_id = payload.get("run_id")

    await enqueue(
        db, "interp_photo", BOT_ID, chat_id,
        payload={
            "image_b64": photo_b64,
            "image_media_type": "image/jpeg",
            "state_payload": payload,
            "role": state.role or "specialist",
        },
        user_id=user_id, context_id=state.context_id, run_id=run_id,
    )
