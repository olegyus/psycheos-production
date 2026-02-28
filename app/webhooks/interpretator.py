"""
Webhook handler for Interpretator bot (Phase 4 → Phase 6 async).

State machine (bot_chat_state.state):
  active                — session opened via /start {jti}, awaiting first material
  intake                — INTAKE job running; worker may ask a clarifying question
  clarification_questions — specialist answering worker-generated questions one by one
  completed             — interpretation sent; session closed

All Claude API calls live in app/worker/handlers/interpretator.py.
The webhook now:
  1. Downloads + base64-encodes photos (no Claude).
  2. Appends material to accumulated_material.
  3. Persists state.
  4. Enqueues the appropriate worker job.
  5. Sends an immediate ack message.
  6. In clarification_questions state: collects answers directly, sends next
     question or enqueues interp_run when all questions answered.
"""
import base64
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot, Update

from app.models.bot_chat_state import BotChatState
from app.services.job_queue import enqueue, is_job_pending_for_chat
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
            "run_id": str(token.jti),   # jti = billing key (matches reserve_stars in pro.py)
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
    current_state = state.state if state else None

    if current_state not in ("active", "intake", "clarification_loop", "clarification_questions"):
        if current_state == "completed":
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

    # Clarification questions state is handled separately — no job enqueue per answer.
    if current_state == "clarification_questions":
        await _handle_clarification_answer(bot, db, text, state, chat_id, user_id)
        return

    # Guard: do not double-enqueue if a job is still running for this chat.
    if await is_job_pending_for_chat(db, BOT_ID, chat_id):
        await bot.send_message(
            chat_id=chat_id,
            text="⏳ Ещё анализирую предыдущее сообщение...",
        )
        return

    payload = dict(state.state_payload or {})
    payload.setdefault("accumulated_material", []).append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "content": text,
    })
    # When specialist answers a clarifying question in intake/clarification_loop,
    # record it separately for the interpretation prompt.
    if current_state in ("intake", "clarification_loop"):
        payload.setdefault("clarifications_received", []).append(text)

    # Persist state with new material before enqueuing (data safety).
    await upsert_chat_state(
        db, bot_id=BOT_ID, chat_id=chat_id, state=current_state,
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


# ── Clarification questions state ─────────────────────────────────────────────

async def _handle_clarification_answer(
    bot: Bot, db: AsyncSession, text: str,
    state: BotChatState, chat_id: int, user_id: int | None,
) -> None:
    """
    Process one specialist answer in the clarification_questions state.

    Records the answer, then either:
    - sends the next question (if more remain), or
    - enqueues interp_run with the full Q&A payload (if all answered).
    """
    payload = dict(state.state_payload or {})
    questions: list = payload.get("questions", [])
    question_index: int = payload.get("question_index", 0)
    clarification_qa: list = payload.setdefault("clarification_qa", [])

    current_q = questions[question_index] if question_index < len(questions) else ""
    clarification_qa.append({"question": current_q, "answer": text})
    question_index += 1
    payload["question_index"] = question_index

    if question_index < len(questions):
        # More questions remain — persist and send the next one.
        next_q = questions[question_index]
        total = len(questions)
        await upsert_chat_state(
            db, bot_id=BOT_ID, chat_id=chat_id, state="clarification_questions",
            state_payload=payload, user_id=user_id,
            role=state.role, context_id=state.context_id,
        )
        await bot.send_message(
            chat_id=chat_id,
            text=f"Вопрос {question_index + 1} из {total}:\n\n{next_q}",
        )
    else:
        # All questions answered — persist and enqueue interpretation.
        run_id = payload.get("run_id")
        await upsert_chat_state(
            db, bot_id=BOT_ID, chat_id=chat_id, state="clarification_questions",
            state_payload=payload, user_id=user_id,
            role=state.role, context_id=state.context_id,
        )
        await enqueue(
            db, "interp_run", BOT_ID, chat_id,
            payload={"state_payload": payload, "role": state.role or "specialist"},
            user_id=user_id, context_id=state.context_id, run_id=run_id,
        )
        await bot.send_message(
            chat_id=chat_id,
            text="⏳ Формирую интерпретацию...",
        )


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
