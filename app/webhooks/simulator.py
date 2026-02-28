"""
Webhook handler for Simulator bot (Phase 4).

State machine (bot_chat_state.state):
  setup    — /start verified; specialist configuring case & mode
  active   — simulation running; messages → Claude
  complete — session finished

state_payload keys:
  run_id          — from link token
  setup_step      — "mode" | "case" | "goal" | "upload" | "crisis" | "goal_practice"
  mode            — "TRAINING" | "PRACTICE"
  case_key        — "1" | "2" | "3"
  crisis          — "NONE" | "MODERATE" | "HIGH"
  custom_data     — specialist-uploaded case text (PRACTICE only)
  custom_prompt   — full system prompt for custom case (PRACTICE only)
  session         — SessionData.model_dump(mode="json")
  profile         — SpecialistProfile.model_dump(mode="json") | null
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update

from app.config import settings
from app.models.bot_chat_state import BotChatState
from app.services.job_queue import enqueue
from app.services.links import LinkVerifyError, verify_link
from app.services.simulator.cases import BUILTIN_CASES
from app.services.simulator.formatter import _escape_html
from app.services.simulator.goals import GOAL_LABELS, MODE_LABELS
from app.services.simulator.schemas import (
    CrisisFlag, SessionData, SessionGoal, SessionMode,
)
from app.services.simulator.system_prompt import build_system_prompt
from app.webhooks.common import upsert_chat_state

logger = logging.getLogger(__name__)

BOT_ID = "simulator"


# ── Keyboards ─────────────────────────────────────────────────────────────────

def _mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎓 Обучение — готовые кейсы", callback_data="mode:TRAINING")],
        [InlineKeyboardButton("🏋️ Тренировка — свои данные", callback_data="mode:PRACTICE")],
    ])


def _case_keyboard() -> InlineKeyboardMarkup:
    crisis_icon = {"NONE": "⚪", "MODERATE": "🟡", "HIGH": "🔴"}
    buttons = []
    for key, case in BUILTIN_CASES.items():
        icon = crisis_icon.get(case.crisis_flag.value, "")
        label = f"{key}. {case.case_name} {icon} CCI:{case.cci.cci:.2f}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"case:{key}")])
    return InlineKeyboardMarkup(buttons)


def _goal_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for goal, label in GOAL_LABELS.items():
        buttons.append([InlineKeyboardButton(label, callback_data=f"goal:{goal.value}")])
    return InlineKeyboardMarkup(buttons)


def _crisis_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚪ Нет кризиса", callback_data="crisis:NONE")],
        [InlineKeyboardButton("🟡 Умеренный", callback_data="crisis:MODERATE")],
        [InlineKeyboardButton("🔴 Высокий", callback_data="crisis:HIGH")],
    ])


def _confirm_end_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Да, завершить", callback_data="end:confirm"),
            InlineKeyboardButton("❌ Продолжить", callback_data="end:cancel"),
        ],
    ])


# ── Entry point ───────────────────────────────────────────────────────────────

async def handle_simulator(
    update: Update, bot: Bot, db: AsyncSession,
    state: BotChatState | None, chat_id: int, user_id: int | None,
) -> None:
    # Callback queries (button presses)
    if update.callback_query:
        cq = update.callback_query
        await cq.answer()
        await _handle_callback(cq, bot, db, state, chat_id, user_id)
        return

    msg = update.message
    if not msg:
        return

    text = (msg.text or "").strip()

    # /start {jti}
    if text.startswith("/start"):
        parts = text.split(" ", 1)
        if len(parts) == 2 and parts[1].strip():
            await _start_session(bot, db, chat_id, user_id, parts[1].strip())
        else:
            await bot.send_message(
                chat_id=chat_id,
                text="❌ Доступ ограничен.\n\nЗапустите инструмент через Pro.",
            )
        return

    # Commands
    if text.startswith("/end"):
        await _cmd_end(bot, state, chat_id)
        return
    if text.startswith("/state"):
        await _cmd_state(bot, state, chat_id)
        return
    if text.startswith("/help"):
        await _cmd_help(bot, chat_id)
        return
    if text.startswith("/pause"):
        await _cmd_pause(bot, state, chat_id)
        return

    # No active session guard
    if not state or state.state not in ("setup", "active"):
        await bot.send_message(
            chat_id=chat_id,
            text="Для запуска используйте ссылку из Pro.",
        )
        return

    payload = dict(state.state_payload or {})
    setup_step = payload.get("setup_step")

    # PRACTICE: handle uploaded data
    if state.state == "setup" and setup_step == "upload":
        if msg.document:
            await _handle_upload_document(bot, db, msg, state, chat_id, user_id, payload)
            return
        if msg.text and not msg.text.startswith("/"):
            await _handle_upload_text(bot, db, state, chat_id, user_id, msg.text, payload)
            return

    # Active session: specialist's message → Claude
    if state.state == "active" and msg.text and not msg.text.startswith("/"):
        await _handle_specialist_message(bot, db, state, chat_id, user_id, msg.text, payload)
        return

    # Fallback for setup states waiting for button presses
    if state.state == "setup":
        await bot.send_message(
            chat_id=chat_id,
            text="Используйте кнопки для навигации.",
        )


# ── Callback router ────────────────────────────────────────────────────────────

async def _handle_callback(
    cq, bot: Bot, db: AsyncSession,
    state: BotChatState | None, chat_id: int, user_id: int | None,
) -> None:
    data = cq.data or ""

    if not state or state.state not in ("setup", "active"):
        return

    payload = dict(state.state_payload or {})
    setup_step = payload.get("setup_step")

    if data.startswith("mode:"):
        await _on_mode_selected(
            cq.message, bot, db, state, chat_id, user_id,
            data.split(":")[1], payload,
        )
    elif data.startswith("case:"):
        await _on_case_selected(
            cq.message, bot, db, state, chat_id, user_id,
            data.split(":")[1], payload,
        )
    elif data.startswith("goal:") and setup_step == "goal":
        await _on_goal_selected_training(
            cq.message, bot, db, state, chat_id, user_id,
            data.split(":")[1], payload,
        )
    elif data.startswith("goal:") and setup_step == "goal_practice":
        await _on_goal_selected_practice(
            cq.message, bot, db, state, chat_id, user_id,
            data.split(":")[1], payload,
        )
    elif data.startswith("crisis:"):
        await _on_crisis_selected(
            cq.message, bot, db, state, chat_id, user_id,
            data.split(":")[1], payload,
        )
    elif data == "end:confirm" and state.state == "active":
        await _on_end_confirm(cq.message, bot, db, state, chat_id, user_id, payload)
    elif data == "end:cancel":
        await cq.message.edit_text("Сессия продолжается. Пишите реплику.")


# ── Session start ──────────────────────────────────────────────────────────────

async def _start_session(
    bot: Bot, db: AsyncSession,
    chat_id: int, user_id: int | None, raw_token: str,
) -> None:
    try:
        token = await verify_link(db, raw_token=raw_token, service_id=BOT_ID, subject_id=user_id)
    except LinkVerifyError as e:
        logger.info(f"[{BOT_ID}] verify_link failed for user {user_id}: {e}")
        await bot.send_message(
            chat_id=chat_id,
            text=f"❌ Доступ закрыт: {e}\n\nВернитесь в Pro и запросите новую ссылку.",
        )
        return

    payload = {
        "run_id": str(token.run_id),
        "setup_step": "mode",
    }
    await upsert_chat_state(
        db,
        bot_id=BOT_ID,
        chat_id=chat_id,
        state="setup",
        state_payload=payload,
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
        text="🔬 <b>PsycheOS Simulator v1.1</b>\n\nВыберите режим работы:",
        parse_mode="HTML",
        reply_markup=_mode_keyboard(),
    )


# ── Setup flow ─────────────────────────────────────────────────────────────────

async def _on_mode_selected(
    msg, bot: Bot, db: AsyncSession, state: BotChatState,
    chat_id: int, user_id: int | None, mode_value: str, payload: dict,
) -> None:
    payload = {**payload, "mode": mode_value}

    if mode_value == "TRAINING":
        payload["setup_step"] = "case"
        await upsert_chat_state(
            db, bot_id=BOT_ID, chat_id=chat_id, state="setup",
            state_payload=payload, user_id=user_id,
            context_id=state.context_id,
        )
        await msg.edit_text(
            "🎓 <b>Режим: Обучение</b>\n"
            "Сигнал супервизора + объяснение динамики\n\n"
            "Выберите кейс:",
            parse_mode="HTML",
            reply_markup=_case_keyboard(),
        )

    elif mode_value == "PRACTICE":
        payload["setup_step"] = "upload"
        await upsert_chat_state(
            db, bot_id=BOT_ID, chat_id=chat_id, state="setup",
            state_payload=payload, user_id=user_id,
            context_id=state.context_id,
        )
        await msg.edit_text(
            "🏋️ <b>Режим: Тренировка</b>\n"
            "Только сигнал супервизора (без объяснений)\n\n"
            "📎 Загрузите данные клиента.\n\n"
            "Отправьте текстом или файлом (.txt / .docx):\n"
            "— Screen-профиль\n"
            "— L0–L4 описания\n"
            "— Концептуализация (Layer A/B/C)\n\n"
            "<i>Или отправьте JSON с данными кейса.</i>",
            parse_mode="HTML",
        )


async def _on_case_selected(
    msg, bot: Bot, db: AsyncSession, state: BotChatState,
    chat_id: int, user_id: int | None, case_key: str, payload: dict,
) -> None:
    case = BUILTIN_CASES.get(case_key)
    if not case:
        await bot.send_message(chat_id=chat_id, text="❌ Кейс не найден.")
        return

    crisis_icon = {"NONE": "⚪", "MODERATE": "🟡", "HIGH": "🔴"}
    payload = {**payload, "case_key": case_key, "setup_step": "goal"}
    await upsert_chat_state(
        db, bot_id=BOT_ID, chat_id=chat_id, state="setup",
        state_payload=payload, user_id=user_id,
        context_id=state.context_id,
    )
    await msg.edit_text(
        f"📋 <b>{case.case_name}</b>\n"
        f"👤 {case.client.gender}, {case.client.age} лет\n"
        f"⚠️ Кризис: {crisis_icon.get(case.crisis_flag.value, '')} {case.crisis_flag.value}\n"
        f"📊 Сложность: {case.difficulty}\n\n"
        "Выберите цель сессии:",
        parse_mode="HTML",
        reply_markup=_goal_keyboard(),
    )


async def _on_goal_selected_training(
    msg, bot: Bot, db: AsyncSession, state: BotChatState,
    chat_id: int, user_id: int | None, goal_value: str, payload: dict,
) -> None:
    try:
        goal = SessionGoal(goal_value)
    except ValueError:
        await bot.send_message(chat_id=chat_id, text="❌ Неизвестная цель.")
        return

    case_key = payload.get("case_key", "1")
    case = BUILTIN_CASES.get(case_key, list(BUILTIN_CASES.values())[0])
    await _launch_session(msg, bot, db, state, chat_id, user_id, case, goal, SessionMode.TRAINING, payload)


async def _handle_upload_text(
    bot: Bot, db: AsyncSession, state: BotChatState,
    chat_id: int, user_id: int | None, text: str, payload: dict,
) -> None:
    payload = {**payload, "custom_data": text, "setup_step": "crisis"}
    await upsert_chat_state(
        db, bot_id=BOT_ID, chat_id=chat_id, state="setup",
        state_payload=payload, user_id=user_id,
        context_id=state.context_id,
    )
    await bot.send_message(
        chat_id=chat_id,
        text="✅ Данные получены.\n\nВыберите кризисный флаг:",
        reply_markup=_crisis_keyboard(),
    )


async def _handle_upload_document(
    bot: Bot, db: AsyncSession, msg, state: BotChatState,
    chat_id: int, user_id: int | None, payload: dict,
) -> None:
    try:
        file = await bot.get_file(msg.document.file_id)
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        buf.seek(0)
        content = buf.read().decode("utf-8", errors="replace")
    except Exception as e:
        await bot.send_message(
            chat_id=chat_id,
            text=f"❌ Ошибка чтения файла: {e}\nПопробуйте отправить текстом.",
        )
        return

    payload = {**payload, "custom_data": content, "setup_step": "crisis"}
    await upsert_chat_state(
        db, bot_id=BOT_ID, chat_id=chat_id, state="setup",
        state_payload=payload, user_id=user_id,
        context_id=state.context_id,
    )
    await bot.send_message(
        chat_id=chat_id,
        text="✅ Файл получен и обработан.\n\nВыберите кризисный флаг:",
        reply_markup=_crisis_keyboard(),
    )


async def _on_crisis_selected(
    msg, bot: Bot, db: AsyncSession, state: BotChatState,
    chat_id: int, user_id: int | None, crisis_value: str, payload: dict,
) -> None:
    payload = {**payload, "crisis": crisis_value, "setup_step": "goal_practice"}
    await upsert_chat_state(
        db, bot_id=BOT_ID, chat_id=chat_id, state="setup",
        state_payload=payload, user_id=user_id,
        context_id=state.context_id,
    )
    await msg.edit_text(
        f"⚠️ Кризис: {crisis_value}\n\nВыберите цель сессии:",
        parse_mode="HTML",
        reply_markup=_goal_keyboard(),
    )


async def _on_goal_selected_practice(
    msg, bot: Bot, db: AsyncSession, state: BotChatState,
    chat_id: int, user_id: int | None, goal_value: str, payload: dict,
) -> None:
    try:
        goal = SessionGoal(goal_value)
    except ValueError:
        await bot.send_message(chat_id=chat_id, text="❌ Неизвестная цель.")
        return

    custom_data = payload.get("custom_data", "")
    crisis_value = payload.get("crisis", "NONE")
    await _launch_session_custom(
        msg, bot, db, state, chat_id, user_id,
        custom_data, goal, SessionMode.PRACTICE, crisis_value, payload,
    )


# ── Session launch ─────────────────────────────────────────────────────────────

async def _launch_session(
    msg, bot: Bot, db: AsyncSession, state: BotChatState,
    chat_id: int, user_id: int | None,
    case, goal: SessionGoal, mode: SessionMode, payload: dict,
) -> None:
    """Enqueue sim_launch job; worker initialises session and sends first reply."""
    case_key_map = {v.case_id: k for k, v in BUILTIN_CASES.items()}
    case_key = case_key_map.get(case.case_id, "1")

    await msg.edit_text("⏳ Инициализация симуляции...")
    await enqueue(
        db, "sim_launch", BOT_ID, chat_id,
        payload={
            "case_key": case_key,
            "goal": goal.value,
            "mode": mode.value,
            "crisis": case.crisis_flag.value,
            "state_payload": payload,
            "role": state.role or "specialist",
        },
        user_id=user_id, context_id=state.context_id, run_id=payload.get("run_id"),
        priority=3,
    )


async def _launch_session_custom(
    msg, bot: Bot, db: AsyncSession, state: BotChatState,
    chat_id: int, user_id: int | None,
    custom_data: str, goal: SessionGoal, mode: SessionMode,
    crisis_value: str, payload: dict,
) -> None:
    """Enqueue sim_launch_custom job; worker builds prompt and starts the session."""
    await msg.edit_text("⏳ Инициализация симуляции с вашими данными...")
    await enqueue(
        db, "sim_launch_custom", BOT_ID, chat_id,
        payload={
            "custom_data": custom_data,
            "goal": goal.value,
            "mode": mode.value,
            "crisis_value": crisis_value,
            "state_payload": payload,
            "role": state.role or "specialist",
        },
        user_id=user_id, context_id=state.context_id, run_id=payload.get("run_id"),
        priority=3,
    )


# ── Active session ─────────────────────────────────────────────────────────────

async def _handle_specialist_message(
    bot: Bot, db: AsyncSession, state: BotChatState,
    chat_id: int, user_id: int | None, text: str, payload: dict,
) -> None:
    """Enqueue a sim_turn job; worker calls Claude and dispatches the reply via outbox."""
    if "session" not in payload:
        await bot.send_message(
            chat_id=chat_id,
            text="❌ Данные сессии не найдены. Запустите через /start.",
        )
        return

    await enqueue(
        db, "sim_turn", BOT_ID, chat_id,
        payload={
            "text": text,
            "state_payload": payload,
            "role": state.role or "specialist",
        },
        user_id=user_id, context_id=state.context_id, run_id=payload.get("run_id"),
        priority=5,
    )


# ── /end flow ──────────────────────────────────────────────────────────────────

async def _cmd_end(bot: Bot, state: BotChatState | None, chat_id: int) -> None:
    if not state or state.state != "active":
        await bot.send_message(chat_id=chat_id, text="Нет активной сессии.")
        return

    await bot.send_message(
        chat_id=chat_id,
        text="Завершить сессию и получить аналитический отчёт?",
        reply_markup=_confirm_end_keyboard(),
    )


async def _on_end_confirm(
    msg, bot: Bot, db: AsyncSession, state: BotChatState,
    chat_id: int, user_id: int | None, payload: dict,
) -> None:
    """Enqueue sim_report job; worker generates docx, saves artifact, updates FSM."""
    if "session" not in payload:
        await msg.edit_text("❌ Данные сессии не найдены.")
        return

    await msg.edit_text("⏳ Формирование аналитического отчёта...")
    await enqueue(
        db, "sim_report", BOT_ID, chat_id,
        payload={
            "session": payload["session"],
            "state_payload": payload,
            "role": state.role or "specialist",
        },
        user_id=user_id, context_id=state.context_id, run_id=payload.get("run_id"),
        priority=3,
    )


# ── Commands ───────────────────────────────────────────────────────────────────

async def _cmd_state(bot: Bot, state: BotChatState | None, chat_id: int) -> None:
    if not state or state.state != "active":
        await bot.send_message(chat_id=chat_id, text="Нет активной сессии. /start для запуска.")
        return

    payload = state.state_payload or {}
    if "session" not in payload:
        await bot.send_message(chat_id=chat_id, text="❌ Данные сессии не найдены.")
        return

    session_data = SessionData.model_validate(payload["session"])
    goal_label = GOAL_LABELS.get(session_data.session_goal, session_data.session_goal.value)
    mode_label = MODE_LABELS.get(session_data.mode.value, session_data.mode.value)

    greens = session_data.signal_log.count("🟢")
    yellows = session_data.signal_log.count("🟡")
    reds = session_data.signal_log.count("🔴")
    exchanges = len(session_data.iteration_log)

    last_info = ""
    if session_data.iteration_log:
        last = session_data.iteration_log[-1]
        last_info = (
            f"\n\n📈 <b>Последняя реплика:</b>\n"
            f"Layer: {last.active_layer_before} | "
            f"Match: {last.regulatory_match_score:.2f} | "
            f"Cascade: {last.cascade_probability:.2f}\n"
            f"Δtrust={last.delta.trust:+d} "
            f"Δtension={last.delta.tension_L0:+d} "
            f"Δuncertainty={last.delta.uncertainty:+d}"
        )

    await bot.send_message(
        chat_id=chat_id,
        text=(
            f"📊 <b>Состояние сессии</b>\n\n"
            f"📋 Кейс: {_escape_html(session_data.case_name)}\n"
            f"🎯 Цель: {_escape_html(goal_label)}\n"
            f"📖 Режим: {_escape_html(mode_label)}\n"
            f"⚠️ Кризис: {session_data.crisis_flag.value}\n\n"
            f"🔄 FSM: <b>{session_data.fsm_state.value}</b>\n"
            f"💬 Реплик: {exchanges}\n"
            f"🟢 {greens}  🟡 {yellows}  🔴 {reds}\n"
            f"📈 Траектория: {' → '.join(session_data.fsm_log[-10:])}"
            f"{last_info}"
        ),
        parse_mode="HTML",
    )


async def _cmd_help(bot: Bot, chat_id: int) -> None:
    await bot.send_message(
        chat_id=chat_id,
        text=(
            "🔬 <b>PsycheOS Simulator v1.1 — Команды</b>\n\n"
            "/end — Завершить сессию → аналитический отчёт (.docx)\n"
            "/state — Текущее состояние сессии\n"
            "/pause — Пауза\n"
            "/help — Эта справка\n\n"
            "<i>Во время сессии просто пишите текстом — "
            "это ваши реплики как специалиста.</i>"
        ),
        parse_mode="HTML",
    )


async def _cmd_pause(bot: Bot, state: BotChatState | None, chat_id: int) -> None:
    if not state or state.state != "active":
        await bot.send_message(chat_id=chat_id, text="Нет активной сессии.")
        return
    await bot.send_message(
        chat_id=chat_id,
        text=(
            "⏸ <b>Пауза</b>\n\n"
            "Сессия приостановлена.\n"
            "Для продолжения просто напишите следующую реплику."
        ),
        parse_mode="HTML",
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_system_prompt(payload: dict, session_data: SessionData) -> str:
    custom = payload.get("custom_prompt")
    if custom:
        return custom
    case_map = {v.case_id: k for k, v in BUILTIN_CASES.items()}
    case_key = case_map.get(session_data.case_id, "1")
    case = BUILTIN_CASES.get(case_key, list(BUILTIN_CASES.values())[0])
    return build_system_prompt(case, session_data.session_goal, session_data.mode)


