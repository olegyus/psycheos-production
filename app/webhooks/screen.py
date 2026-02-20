"""Screen v2 webhook handler — full multi-phase screening flow.

FSM states:
  idle/None  → /start {jti}: verify link token, load assessment, show welcome
  active     → "start_screening" callback: start assessment, show first screen
  phase1     → toggle_{idx} + confirm_selection: process Phase 1 responses
  phase2     → toggle_{idx} + confirm_selection: process Phase 2 responses
  phase3     → toggle_{idx} + confirm_selection: process Phase 3 responses
  completed  → any message → "Скрининг завершён"
"""
from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update

from app.config import settings
from app.models.bot_chat_state import BotChatState
from app.models.context import Context
from app.models.screening_assessment import ScreeningAssessment
from app.services.links import LinkVerifyError, verify_link
from app.services.screen.orchestrator import ScreenOrchestrator
from app.webhooks.common import upsert_chat_state

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def handle_screen(
    update: Update,
    bot: Bot,
    db: AsyncSession,
    state: BotChatState | None,
    chat_id: int,
    user_id: int | None,
) -> None:
    if update.callback_query:
        await _handle_callback(update, bot, db, state, chat_id, user_id)
        return

    if update.message and update.message.text:
        await _handle_message(update, bot, db, state, chat_id, user_id)
        return


# ---------------------------------------------------------------------------
# Message handler
# ---------------------------------------------------------------------------

async def _handle_message(
    update: Update,
    bot: Bot,
    db: AsyncSession,
    state: BotChatState | None,
    chat_id: int,
    user_id: int | None,
) -> None:
    text = update.message.text.strip()
    current_state = state.state if state else None

    if text.startswith("/start"):
        parts = text.split(" ", 1)
        if len(parts) == 2 and parts[1].strip():
            await _handle_start_token(bot, db, chat_id, user_id, parts[1].strip())
        else:
            await bot.send_message(
                chat_id=chat_id,
                text="❌ Доступ ограничен.\n\nОжидайте ссылку от специалиста.",
            )
        return

    if current_state == "completed":
        await bot.send_message(
            chat_id=chat_id,
            text="✅ Скрининг завершён. Результаты переданы вашему специалисту.",
        )
        return

    if current_state in ("phase1", "phase2", "phase3"):
        await bot.send_message(
            chat_id=chat_id,
            text="Пожалуйста, используйте кнопки для ответа.",
        )
        return

    await bot.send_message(
        chat_id=chat_id,
        text="Для запуска скрининга используйте ссылку от специалиста.",
    )


# ---------------------------------------------------------------------------
# /start token verification
# ---------------------------------------------------------------------------

async def _handle_start_token(
    bot: Bot,
    db: AsyncSession,
    chat_id: int,
    user_id: int,
    raw_token: str,
) -> None:
    try:
        token = await verify_link(
            db,
            raw_token=raw_token,
            service_id="screen",
            subject_id=user_id,
        )
    except LinkVerifyError as e:
        logger.info(f"[screen] verify_link failed for user {user_id}: {e}")
        await bot.send_message(
            chat_id=chat_id,
            text=f"❌ Доступ закрыт: {e}\n\nВернитесь к специалисту для получения новой ссылки.",
        )
        return

    result = await db.execute(
        select(ScreeningAssessment).where(ScreeningAssessment.link_token_jti == token.jti)
    )
    assessment = result.scalar_one_or_none()

    if not assessment:
        await bot.send_message(
            chat_id=chat_id,
            text="❌ Сессия скрининга не найдена.\n\nОбратитесь к специалисту.",
        )
        return

    if assessment.status == "completed":
        await bot.send_message(
            chat_id=chat_id,
            text="✅ Этот скрининг уже завершён. Результаты переданы вашему специалисту.",
        )
        return

    await upsert_chat_state(
        db,
        bot_id="screen",
        chat_id=chat_id,
        state="active",
        state_payload={
            "assessment_id": str(assessment.id),
            "run_id": str(token.run_id),
        },
        user_id=user_id,
        role="client",
        context_id=token.context_id,
    )

    logger.info(
        f"[screen] Session started: user={user_id} "
        f"assessment={assessment.id} context={token.context_id}"
    )

    await bot.send_message(
        chat_id=chat_id,
        text=(
            "👋 Добро пожаловать в PsycheOS Screen!\n\n"
            "Этот короткий скрининг поможет вашему специалисту лучше понять "
            "ваше текущее состояние.\n\n"
            "📋 Вас ждут несколько вопросов с вариантами ответа.\n"
            "Вы можете выбирать несколько вариантов одновременно.\n\n"
            "Нажмите «Начать», когда будете готовы."
        ),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ Начать скрининг", callback_data="start_screening")],
        ]),
    )


# ---------------------------------------------------------------------------
# Callback handler
# ---------------------------------------------------------------------------

async def _handle_callback(
    update: Update,
    bot: Bot,
    db: AsyncSession,
    state: BotChatState | None,
    chat_id: int,
    user_id: int | None,
) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    current_state = state.state if state else None
    payload = (state.state_payload or {}) if state else {}

    # ── start_screening ──────────────────────────────────────────────────
    if data == "start_screening" and current_state == "active":
        assessment_id_str = payload.get("assessment_id")
        if not assessment_id_str:
            await bot.send_message(
                chat_id=chat_id,
                text="❌ Ошибка сессии. Используйте ссылку от специалиста.",
            )
            return

        orchestrator = ScreenOrchestrator(db)
        result = await orchestrator.start_assessment(UUID(assessment_id_str))

        if result["action"] == "show_screen":
            new_payload = dict(payload)
            new_payload["current_screen"] = result["screen"]
            new_payload["screen_index"] = result.get("screen_index", 0)
            new_payload["selected_options"] = []
            new_payload["phase"] = result["phase"]
            await upsert_chat_state(
                db, "screen", chat_id, "phase1",
                state_payload=new_payload, user_id=user_id, role="client",
                context_id=state.context_id if state else None,
            )
            screen_index = result.get("screen_index", 0)
            header = f"📋 Вопрос {screen_index + 1} из 6" if result["phase"] == 1 else None
            await _show_multi_select(bot, chat_id, result["screen"], [], header=header)
        elif result["action"] == "complete":
            await _handle_completion(bot, db, chat_id, user_id, state, result)
        return

    # ── toggle_{idx} ─────────────────────────────────────────────────────
    if data.startswith("toggle_") and current_state in ("phase1", "phase2", "phase3"):
        try:
            idx = int(data[len("toggle_"):])
        except ValueError:
            return

        selected = list(payload.get("selected_options", []))
        if idx in selected:
            selected.remove(idx)
        else:
            selected.append(idx)

        new_payload = dict(payload)
        new_payload["selected_options"] = selected
        await upsert_chat_state(
            db, "screen", chat_id, current_state,
            state_payload=new_payload, user_id=user_id, role="client",
            context_id=state.context_id if state else None,
        )
        await _update_multi_select(query, payload.get("current_screen", {}), selected)
        return

    # ── confirm_selection ────────────────────────────────────────────────
    if data == "confirm_selection" and current_state in ("phase1", "phase2", "phase3"):
        selected = payload.get("selected_options", [])
        if not selected:
            await query.answer("Выберите хотя бы один вариант.", show_alert=True)
            return

        assessment_id_str = payload.get("assessment_id")
        if not assessment_id_str:
            await bot.send_message(chat_id=chat_id, text="❌ Ошибка сессии.")
            return

        # Remove keyboard immediately so the user sees feedback, then show typing
        await query.edit_message_reply_markup(reply_markup=None)
        await bot.send_chat_action(chat_id=chat_id, action="typing")

        orchestrator = ScreenOrchestrator(db)
        assessment_id = UUID(assessment_id_str)
        current_screen = payload.get("current_screen", {})

        if current_state == "phase1":
            result = await orchestrator.process_phase1_response(
                assessment_id,
                payload.get("screen_index", 0),
                selected,
            )
        elif current_state == "phase2":
            result = await orchestrator.process_phase2_response(
                assessment_id, selected, current_screen
            )
        else:  # phase3
            result = await orchestrator.process_phase3_response(
                assessment_id, selected, current_screen
            )

        if result["action"] == "show_screen":
            next_phase = result.get("phase", int(current_state[-1]))
            next_state = f"phase{next_phase}"
            new_payload = dict(payload)
            new_payload["current_screen"] = result["screen"]
            new_payload["screen_index"] = result.get("screen_index", 0)
            new_payload["selected_options"] = []
            new_payload["phase"] = next_phase
            await upsert_chat_state(
                db, "screen", chat_id, next_state,
                state_payload=new_payload, user_id=user_id, role="client",
                context_id=state.context_id if state else None,
            )
            # Notify client when entering a new phase
            if next_state != current_state:
                await _show_phase_transition(bot, chat_id, current_state, next_state)
            screen_idx = result.get("screen_index", 0)
            ph1_header = f"📋 Вопрос {screen_idx + 1} из 6" if next_phase == 1 else None
            await _show_multi_select(bot, chat_id, result["screen"], [], header=ph1_header)
        elif result["action"] == "complete":
            await bot.send_message(chat_id=chat_id, text="⏳ Анализирую ваши ответы...")
            await _handle_completion(bot, db, chat_id, user_id, state, result)
        return


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

_PHASE_TRANSITION_TEXTS: dict[tuple[str, str], str] = {
    ("phase1", "phase2"): (
        "✅ Первая часть пройдена.\n\n"
        "📝 Переходим к уточняющим вопросам — их будет немного, "
        "они помогут лучше понять вашу ситуацию."
    ),
    ("phase2", "phase3"): (
        "✅ Основные вопросы пройдены.\n\n"
        "🔍 Последний блок — несколько дополнительных вопросов для уточнения."
    ),
}


async def _show_phase_transition(
    bot: Bot, chat_id: int, from_state: str, to_state: str
) -> None:
    """Send a brief transition message when moving between phases."""
    text = _PHASE_TRANSITION_TEXTS.get((from_state, to_state))
    if text:
        await bot.send_message(chat_id=chat_id, text=text)


async def _show_multi_select(
    bot: Bot, chat_id: int, screen: dict, selected: list[int], header: str | None = None
) -> None:
    """Send a new multi-select question message."""
    question = screen.get("question", "")
    if header:
        question = f"{header}\n\n{question}"
    options = screen.get("options", [])

    buttons = []
    for i, opt in enumerate(options):
        mark = "✅" if i in selected else "⬜"
        buttons.append(
            [InlineKeyboardButton(f"{mark} {opt['text']}", callback_data=f"toggle_{i}")]
        )
    buttons.append([InlineKeyboardButton("Подтвердить ✓", callback_data="confirm_selection")])

    await bot.send_message(
        chat_id=chat_id,
        text=question,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def _update_multi_select(query, screen: dict, selected: list[int]) -> None:
    """Edit the keyboard of an existing multi-select message to reflect toggle state."""
    options = screen.get("options", [])

    buttons = []
    for i, opt in enumerate(options):
        mark = "✅" if i in selected else "⬜"
        buttons.append(
            [InlineKeyboardButton(f"{mark} {opt['text']}", callback_data=f"toggle_{i}")]
        )
    buttons.append([InlineKeyboardButton("Подтвердить ✓", callback_data="confirm_selection")])

    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------

async def _handle_completion(
    bot: Bot,
    db: AsyncSession,
    chat_id: int,
    user_id: int | None,
    state: BotChatState | None,
    result: dict,
) -> None:
    """Mark assessment complete in FSM, notify client, notify specialist."""
    payload = (state.state_payload or {}) if state else {}
    new_payload = {k: v for k, v in payload.items() if k not in ("current_screen", "selected_options")}

    await upsert_chat_state(
        db, "screen", chat_id, "completed",
        state_payload=new_payload, user_id=user_id, role="client",
        context_id=state.context_id if state else None,
    )

    await bot.send_message(
        chat_id=chat_id,
        text=(
            "✅ *Скрининг завершён!*\n\n"
            "Спасибо за ваши ответы. Результаты переданы вашему специалисту.\n\n"
            "_Специалист свяжется с вами для обсуждения результатов._"
        ),
        parse_mode="Markdown",
    )

    # Notify specialist via Pro bot
    assessment_id_str = payload.get("assessment_id")
    if assessment_id_str:
        await _notify_specialist(db, assessment_id_str, state.context_id if state else None)


async def _notify_specialist(
    db: AsyncSession, assessment_id_str: str, context_id
) -> None:
    """Send a completion notification to the specialist in the Pro bot.

    specialist_user_id is taken from ScreeningAssessment (BigInteger Telegram ID).
    Context is loaded only to get client_ref for the label.
    """
    try:
        assessment_result = await db.execute(
            select(ScreeningAssessment).where(
                ScreeningAssessment.id == UUID(assessment_id_str)
            )
        )
        assessment = assessment_result.scalar_one_or_none()
        if not assessment:
            logger.warning("[screen] _notify_specialist: assessment %s not found", assessment_id_str)
            return

        specialist_telegram_id: int = assessment.specialist_user_id  # BigInteger Telegram ID

        label: str = str(assessment_id_str)[:8]
        if context_id:
            ctx_result = await db.execute(
                select(Context).where(Context.context_id == context_id)
            )
            ctx = ctx_result.scalar_one_or_none()
            if ctx and ctx.client_ref:
                label = ctx.client_ref

        from telegram import Bot as TgBot
        pro_bot = TgBot(token=settings.TG_TOKEN_PRO)
        await pro_bot.send_message(
            chat_id=specialist_telegram_id,
            text=(
                f"✅ *Скрининг завершён*\n\n"
                f"Кейс: {label}\n\n"
                f"Для просмотра результатов откройте кейс в меню."
            ),
            parse_mode="Markdown",
        )
    except Exception:
        logger.warning("[screen] Failed to notify specialist", exc_info=True)
