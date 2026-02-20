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

import io
import logging
import re
from datetime import datetime
from typing import Optional

from anthropic import AsyncAnthropic
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update

from app.config import settings
from app.models.bot_chat_state import BotChatState
from app.services.artifacts import save_artifact
from app.services.links import LinkVerifyError, verify_link
from app.services.simulator.cases import BUILTIN_CASES
from app.services.simulator.formatter import (
    _escape_html, build_iteration_log, format_for_telegram, format_intro,
    parse_claude_response,
)
from app.services.simulator.goals import GOAL_LABELS, MODE_LABELS
from app.services.simulator.report_generator import generate_report_docx
from app.services.simulator.schemas import (
    BuiltinCase, CaseDynamics, ContinuumScore, CrisisFlag,
    ClientInfo, Conceptualization, LayerA, LayerB, LayerDescription, Layers,
    ScreenProfile, SessionData, SessionGoal, SessionMode, SpecialistProfile,
    SystemCost, Target, TSIComponents, CCIComponents,
)
from app.services.simulator.system_prompt import build_system_prompt
from app.webhooks.common import upsert_chat_state

logger = logging.getLogger(__name__)

BOT_ID = "simulator"
_ANTHROPIC_MODEL = "claude-sonnet-4-5-20250929"
_MAX_SESSION_HISTORY = 50


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
    case: BuiltinCase, goal: SessionGoal, mode: SessionMode, payload: dict,
) -> None:
    await msg.edit_text("⏳ Инициализация симуляции...")

    system_prompt = build_system_prompt(case, goal, mode)
    session_data = SessionData(
        user_id=user_id or 0,
        case_id=case.case_id,
        case_name=case.case_name,
        mode=mode,
        session_goal=goal,
        crisis_flag=case.crisis_flag,
    )

    first_user_msg = (
        "Сессия начинается. Клиент входит в кабинет. "
        "Сгенерируй первую реплику клиента и начальный блок супервизора."
    )
    session_data.messages.append({"role": "user", "content": first_user_msg})

    try:
        client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        resp = await client.messages.create(
            model=_ANTHROPIC_MODEL,
            max_tokens=2048,
            system=system_prompt,
            messages=session_data.messages,
        )
        claude_response = resp.content[0].text
    except Exception as e:
        logger.exception("[simulator] Claude error during session launch")
        await msg.edit_text(
            f"❌ Ошибка Claude API:\n<code>{_escape_html(str(e))}</code>",
            parse_mode="HTML",
        )
        return

    session_data.messages.append({"role": "assistant", "content": claude_response})

    parsed = parse_claude_response(claude_response)
    if parsed.signal:
        session_data.signal_log.append(parsed.signal)
    if parsed.fsm_state:
        session_data.fsm_log.append(parsed.fsm_state)
    iteration = build_iteration_log(parsed=parsed, replica_id=1, specialist_input=first_user_msg)
    session_data.iteration_log.append(iteration)

    goal_label = GOAL_LABELS.get(goal, goal.value)
    mode_label = MODE_LABELS.get(mode.value, mode.value)
    client_info = f"{case.client.gender}, {case.client.age} лет"

    formatted = format_intro(
        case_name=case.case_name,
        client_info=client_info,
        crisis=case.crisis_flag.value,
        goal=goal_label,
        mode=mode_label,
        first_reply=claude_response,
        cci=case.cci.cci,
    )

    new_payload = {
        **payload,
        "session": session_data.model_dump(mode="json"),
        "setup_step": None,
    }
    await upsert_chat_state(
        db, bot_id=BOT_ID, chat_id=chat_id, state="active",
        state_payload=new_payload, user_id=user_id,
        context_id=state.context_id,
    )

    chunks = _split_text(formatted)
    await msg.edit_text(chunks[0], parse_mode="HTML")
    for chunk in chunks[1:]:
        await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="HTML")


async def _launch_session_custom(
    msg, bot: Bot, db: AsyncSession, state: BotChatState,
    chat_id: int, user_id: int | None,
    custom_data: str, goal: SessionGoal, mode: SessionMode,
    crisis_value: str, payload: dict,
) -> None:
    await msg.edit_text("⏳ Инициализация симуляции с вашими данными...")

    crisis = CrisisFlag(crisis_value)
    baseline_L0 = {"NONE": 35, "MODERATE": 55, "HIGH": 78}.get(crisis_value, 35)

    placeholder_case = BuiltinCase(
        case_id="CUSTOM",
        case_name="Пользовательский кейс",
        difficulty="CUSTOM",
        client=ClientInfo(
            id="CUSTOM", gender="не указан", age=0,
            presenting_complaints=["См. загруженные данные"],
        ),
        screen_profile=ScreenProfile(
            economy_exploration=ContinuumScore(value=50),
            protection_contact=ContinuumScore(value=50),
            retention_movement=ContinuumScore(value=50),
            survival_development=ContinuumScore(value=50),
        ),
        layers=Layers(
            L0=LayerDescription(description="См. загруженные данные"),
            L1=LayerDescription(description="См. загруженные данные"),
            L2=LayerDescription(description="См. загруженные данные"),
            L3=LayerDescription(description="См. загруженные данные"),
            L4=LayerDescription(description="См. загруженные данные"),
        ),
        conceptualization=Conceptualization(
            layer_a=LayerA(
                leading_hypothesis="См. загруженные данные",
                dominant_layer="L0",
                configuration="См. загруженные данные",
                system_cost=SystemCost(),
            ),
            layer_b=LayerB(targets=[], sequence="См. загруженные данные"),
        ),
        dynamics=CaseDynamics(
            baseline_tension_L0=baseline_L0,
            baseline_cognitive_access=max(20, 100 - int(baseline_L0 * 0.8)),
            baseline_uncertainty=65,
            baseline_trust=25,
            L0_reactivity="moderate",
            L2_strength="moderate",
            L3_accessibility="moderate",
            interpretation_tolerance="moderate",
            uncertainty_tolerance="moderate",
            cognitive_window="moderate",
            escalation_speed="moderate",
            intervention_range="moderate",
            recovery_rate=0.5,
            volatility=0.4,
        ),
        crisis_flag=crisis,
    )

    system_prompt = build_system_prompt(placeholder_case, goal, mode)
    custom_block = (
        "\n\n═══════════════════════════════════════════\n"
        "ДАННЫЕ КЛИЕНТА (загружены специалистом):\n"
        "═══════════════════════════════════════════\n"
        f"{custom_data}\n"
        "═══════════════════════════════════════════\n"
        "Используй ЭТИ данные как основу для симуляции. "
        "Извлеки из них Screen-профиль, L0–L4, Layer A/B/C и все остальные параметры. "
        "Если данные неполные — заполни пробелы логически на основе имеющегося.\n"
    )
    full_system_prompt = system_prompt + custom_block

    session_data = SessionData(
        user_id=user_id or 0,
        case_id="CUSTOM",
        case_name="Пользовательский кейс",
        mode=mode,
        session_goal=goal,
        crisis_flag=crisis,
    )

    first_user_msg = (
        "Сессия начинается. Клиент входит в кабинет. "
        "Сгенерируй первую реплику клиента и начальный блок супервизора."
    )
    session_data.messages.append({"role": "user", "content": first_user_msg})

    try:
        claude_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        resp = await claude_client.messages.create(
            model=_ANTHROPIC_MODEL,
            max_tokens=2048,
            system=full_system_prompt,
            messages=session_data.messages,
        )
        claude_response = resp.content[0].text
    except Exception as e:
        logger.exception("[simulator] Claude error during custom session launch")
        await msg.edit_text(
            f"❌ Ошибка Claude API:\n<code>{_escape_html(str(e))}</code>",
            parse_mode="HTML",
        )
        return

    session_data.messages.append({"role": "assistant", "content": claude_response})

    parsed = parse_claude_response(claude_response)
    if parsed.signal:
        session_data.signal_log.append(parsed.signal)
    if parsed.fsm_state:
        session_data.fsm_log.append(parsed.fsm_state)
    iteration = build_iteration_log(parsed=parsed, replica_id=1, specialist_input=first_user_msg)
    session_data.iteration_log.append(iteration)

    goal_label = GOAL_LABELS.get(goal, goal.value)
    mode_label = MODE_LABELS.get(mode.value, mode.value)

    formatted = format_intro(
        case_name="Пользовательский кейс",
        client_info="по загруженным данным",
        crisis=crisis_value,
        goal=goal_label,
        mode=mode_label,
        first_reply=claude_response,
        cci=placeholder_case.cci.cci,
    )

    new_payload = {
        **payload,
        "custom_prompt": full_system_prompt,
        "session": session_data.model_dump(mode="json"),
        "setup_step": None,
    }
    await upsert_chat_state(
        db, bot_id=BOT_ID, chat_id=chat_id, state="active",
        state_payload=new_payload, user_id=user_id,
        context_id=state.context_id,
    )

    chunks = _split_text(formatted)
    await msg.edit_text(chunks[0], parse_mode="HTML")
    for chunk in chunks[1:]:
        await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="HTML")


# ── Active session ─────────────────────────────────────────────────────────────

async def _handle_specialist_message(
    bot: Bot, db: AsyncSession, state: BotChatState,
    chat_id: int, user_id: int | None, text: str, payload: dict,
) -> None:
    if "session" not in payload:
        await bot.send_message(
            chat_id=chat_id,
            text="❌ Данные сессии не найдены. Запустите через /start.",
        )
        return

    session_data = SessionData.model_validate(payload["session"])
    system_prompt = _get_system_prompt(payload, session_data)

    session_data.messages.append({"role": "user", "content": text})

    if len(session_data.messages) > _MAX_SESSION_HISTORY:
        session_data.messages = (
            session_data.messages[:1]
            + session_data.messages[-(_MAX_SESSION_HISTORY - 1):]
        )

    await bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        claude_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        resp = await claude_client.messages.create(
            model=_ANTHROPIC_MODEL,
            max_tokens=2048,
            system=system_prompt,
            messages=session_data.messages,
        )
        claude_response = resp.content[0].text
    except Exception as e:
        logger.exception("[simulator] Claude error handling specialist message")
        session_data.messages.pop()
        payload = {**payload, "session": session_data.model_dump(mode="json")}
        await upsert_chat_state(
            db, bot_id=BOT_ID, chat_id=chat_id, state="active",
            state_payload=payload, user_id=user_id,
            context_id=state.context_id,
        )
        await bot.send_message(
            chat_id=chat_id,
            text=f"❌ Ошибка при обращении к Claude:\n<code>{_escape_html(str(e))}</code>",
            parse_mode="HTML",
        )
        return

    session_data.messages.append({"role": "assistant", "content": claude_response})

    parsed = parse_claude_response(claude_response)
    if parsed.signal:
        session_data.signal_log.append(parsed.signal)
    if parsed.fsm_state:
        session_data.fsm_log.append(parsed.fsm_state)

    replica_id = len(session_data.iteration_log) + 1
    iteration = build_iteration_log(parsed=parsed, replica_id=replica_id, specialist_input=text)
    session_data.iteration_log.append(iteration)

    payload = {**payload, "session": session_data.model_dump(mode="json")}
    await upsert_chat_state(
        db, bot_id=BOT_ID, chat_id=chat_id, state="active",
        state_payload=payload, user_id=user_id,
        context_id=state.context_id,
    )

    formatted = format_for_telegram(parsed)

    if len(formatted) > 4000:
        client_msg = f"🗣 <b>Клиент:</b>\n{_escape_html(parsed.client_text)}"
        await bot.send_message(chat_id=chat_id, text=client_msg, parse_mode="HTML")
        if parsed.supervisor_block:
            sup_msg = f"{'─' * 30}\n{_escape_html(parsed.supervisor_block)}"
            await bot.send_message(chat_id=chat_id, text=sup_msg, parse_mode="HTML")
    else:
        await bot.send_message(chat_id=chat_id, text=formatted, parse_mode="HTML")


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
    if "session" not in payload:
        await msg.edit_text("❌ Данные сессии не найдены.")
        return

    session_data = SessionData.model_validate(payload["session"])
    await msg.edit_text("⏳ Формирование аналитического отчёта...")

    system_prompt = _get_system_prompt(payload, session_data)
    session_data.messages.append({"role": "user", "content": "/end"})

    try:
        claude_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        resp = await claude_client.messages.create(
            model=_ANTHROPIC_MODEL,
            max_tokens=4096,
            system=system_prompt,
            messages=session_data.messages,
        )
        report_text = resp.content[0].text
    except Exception as e:
        logger.exception("[simulator] Claude error generating report")
        await msg.edit_text(
            f"❌ Ошибка:\n<code>{_escape_html(str(e))}</code>",
            parse_mode="HTML",
        )
        return

    tsi = _parse_tsi_from_report(report_text)
    session_data.tsi = tsi
    cci = _get_cci(session_data.case_id)

    specialist_profile = _update_profile(payload, user_id, session_data, tsi)

    goal_label = GOAL_LABELS.get(session_data.session_goal, session_data.session_goal.value)
    mode_label = MODE_LABELS.get(session_data.mode.value, session_data.mode.value)

    try:
        docx_buf = generate_report_docx(
            report_text=report_text,
            case_name=session_data.case_name,
            case_id=session_data.case_id,
            session_goal=goal_label,
            mode=mode_label,
            crisis_flag=session_data.crisis_flag.value,
            signal_log=session_data.signal_log,
            fsm_log=session_data.fsm_log,
            iteration_log=session_data.iteration_log,
            tsi=tsi,
            cci=cci,
            specialist_profile=specialist_profile,
        )
    except Exception as e:
        logger.error("[simulator] Report generation failed: %s", e)
        await _send_text_fallback(bot, chat_id, report_text, tsi)
        final_payload = {"profile": specialist_profile.model_dump(mode="json") if specialist_profile else {}}
        await upsert_chat_state(
            db, bot_id=BOT_ID, chat_id=chat_id, state="complete",
            state_payload=final_payload, user_id=user_id,
            context_id=state.context_id,
        )
        _tsi_txt = f"TSI: {tsi.tsi:.2f} ({tsi.interpretation})" if tsi else "TSI: н/д"
        await save_artifact(
            db=db,
            run_id=payload.get("run_id"),
            service_id="simulator",
            context_id=state.context_id,
            specialist_telegram_id=user_id,
            payload={
                "tsi": tsi.model_dump(mode="json") if tsi else None,
                "cci": cci.model_dump(mode="json") if cci else None,
                "session_turns": len(session_data.iteration_log),
                "report_text": report_text,
                "profile": specialist_profile.model_dump(mode="json") if specialist_profile else None,
            },
            summary=f"Симуляция (текстовый отчёт). {_tsi_txt}.",
        )
        return

    exchanges = len(session_data.iteration_log)
    greens = session_data.signal_log.count("🟢")
    yellows = session_data.signal_log.count("🟡")
    reds = session_data.signal_log.count("🔴")
    tsi_text = f"TSI: {tsi.tsi:.2f} ({tsi.interpretation})" if tsi else "TSI: н/д"
    cci_text = f"CCI: {cci.cci:.2f}" if cci else ""

    caption = (
        f"📋 <b>Аналитический отчёт v1.1</b>\n\n"
        f"Кейс: {_escape_html(session_data.case_name)}\n"
        f"Реплик: {exchanges} | 🟢{greens} 🟡{yellows} 🔴{reds}\n"
        f"📊 {tsi_text}"
    )
    if cci_text:
        caption += f" | {cci_text}"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"PsycheOS_Report_{session_data.case_id}_{timestamp}.docx"
    doc_file = InputFile(docx_buf, filename=filename)
    await bot.send_document(chat_id=chat_id, document=doc_file, caption=caption, parse_mode="HTML")
    await bot.send_message(
        chat_id=chat_id,
        text="✅ Сессия завершена. Используйте /start для новой симуляции.",
    )

    final_payload = {"profile": specialist_profile.model_dump(mode="json") if specialist_profile else {}}
    await upsert_chat_state(
        db, bot_id=BOT_ID, chat_id=chat_id, state="complete",
        state_payload=final_payload, user_id=user_id,
        context_id=state.context_id,
    )

    _tsi_txt = f"TSI: {tsi.tsi:.2f} ({tsi.interpretation})" if tsi else "TSI: н/д"
    _cci_txt = f" | CCI: {cci.cci:.2f}" if cci else ""
    await save_artifact(
        db=db,
        run_id=payload.get("run_id"),
        service_id="simulator",
        context_id=state.context_id,
        specialist_telegram_id=user_id,
        payload={
            "tsi": tsi.model_dump(mode="json") if tsi else None,
            "cci": cci.model_dump(mode="json") if cci else None,
            "session_turns": exchanges,
            "report_text": report_text,
            "profile": specialist_profile.model_dump(mode="json") if specialist_profile else None,
        },
        summary=f"Симуляция. {_tsi_txt}{_cci_txt}.",
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


def _parse_tsi_from_report(report_text: str) -> Optional[TSIComponents]:
    try:
        def _extract(pattern: str, text: str) -> float:
            m = re.search(pattern, text)
            return float(m.group(1)) if m else 0.0

        r_match = _extract(r'R_match:\s*([\d.]+)', report_text)
        l_cons = _extract(r'L_consistency:\s*([\d.]+)', report_text)
        alliance = _extract(r'Alliance_score:\s*([\d.]+)', report_text)
        unc_mod = _extract(r'Uncertainty_modulation:\s*([\d.]+)', report_text)
        reactivity = _extract(r'Therapist_reactivity:\s*([\d.]+)', report_text)

        if sum(1 for v in [r_match, l_cons, alliance, unc_mod, reactivity] if v > 0) < 3:
            logger.warning("[simulator] TSI parsing: fewer than 3 components found")
            return None

        return TSIComponents(
            R_match=min(1.0, r_match),
            L_consistency=min(1.0, l_cons),
            Alliance_score=min(1.0, alliance),
            Uncertainty_modulation=min(1.0, unc_mod),
            Therapist_reactivity=min(1.0, reactivity),
        )
    except Exception as e:
        logger.error("[simulator] TSI parsing failed: %s", e)
        return None


def _get_cci(case_id: str) -> Optional[CCIComponents]:
    case_map = {v.case_id: v for v in BUILTIN_CASES.values()}
    case = case_map.get(case_id)
    return case.cci if case else None


def _update_profile(
    payload: dict,
    user_id: int | None,
    session_data: SessionData,
    tsi: Optional[TSIComponents],
) -> Optional[SpecialistProfile]:
    existing = payload.get("profile")
    profile = (
        SpecialistProfile.model_validate(existing)
        if existing
        else SpecialistProfile(specialist_id=str(user_id or 0))
    )

    profile.sessions_count += 1
    profile.cases_completed.append(session_data.case_id)

    if tsi:
        profile.tsi_history.append(tsi.tsi)
        profile.average_tsi = round(sum(profile.tsi_history) / len(profile.tsi_history), 2)

    total_signals = len(session_data.signal_log)
    if total_signals > 0:
        yellows = session_data.signal_log.count("🟡")
        reds = session_data.signal_log.count("🔴")
        prev = profile.sessions_count - 1
        if prev > 0:
            profile.yellow_ratio = round(
                (profile.yellow_ratio * prev + yellows / total_signals) / profile.sessions_count, 2
            )
            profile.red_ratio = round(
                (profile.red_ratio * prev + reds / total_signals) / profile.sessions_count, 2
            )
        else:
            profile.yellow_ratio = round(yellows / total_signals, 2)
            profile.red_ratio = round(reds / total_signals, 2)

    if session_data.iteration_log:
        avg_delta = sum(it.delta.trust for it in session_data.iteration_log) / len(session_data.iteration_log)
        prev = profile.sessions_count - 1
        if prev > 0:
            profile.average_delta_trust = round(
                (profile.average_delta_trust * prev + avg_delta) / profile.sessions_count, 2
            )
        else:
            profile.average_delta_trust = round(avg_delta, 2)

    return profile


async def _send_text_fallback(bot: Bot, chat_id: int, report_text: str, tsi=None) -> None:
    header = "📋 <b>АНАЛИТИЧЕСКИЙ ОТЧЁТ</b>\n\n"
    if tsi:
        header += f"📊 TSI: {tsi.tsi:.2f} ({tsi.interpretation})\n\n"
    full_text = header + _escape_html(report_text)
    for chunk in _split_text(full_text):
        await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="HTML")


def _split_text(text: str, max_len: int = 4000) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_pos = text.rfind("\n", 0, max_len)
        if split_pos == -1:
            split_pos = max_len
        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip("\n")
    return chunks
