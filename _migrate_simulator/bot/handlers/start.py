"""Хендлер /start — настройка и запуск сессии.

Поток:
  /start → выбор РЕЖИМА
    → ОБУЧЕНИЕ: выбор кейса → выбор цели → запуск
    → ТРЕНИРОВКА: загрузка данных → выбор кризиса → выбор цели → запуск
"""

import logging

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from bot.keyboards.inline import (
    mode_keyboard, case_keyboard, goal_keyboard, crisis_keyboard,
)
from core import session_manager
from core.claude_client import send_to_claude
from core.formatter import format_intro
from data.cases import BUILTIN_CASES
from data.goals import GOAL_LABELS, MODE_LABELS
from data.schemas import SessionGoal, SessionMode, CrisisFlag
from data.system_prompt import build_system_prompt

logger = logging.getLogger(__name__)

router = Router()


# ── FSM-состояния для настройки (aiogram FSM, не PsycheOS FSM) ────────────

class SetupStates(StatesGroup):
    choosing_mode = State()
    choosing_case = State()         # ОБУЧЕНИЕ
    choosing_goal = State()         # оба
    waiting_upload = State()        # ТРЕНИРОВКА
    choosing_crisis = State()       # ТРЕНИРОВКА
    choosing_goal_practice = State() # ТРЕНИРОВКА


# ── /start ─────────────────────────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """Начало: выбор режима."""

    existing = session_manager.get_session(message.from_user.id)
    if existing:
        await message.answer(
            "⚠️ У вас есть активная сессия. "
            "Используйте /end для завершения или выберите новый кейс (текущая будет закрыта)."
        )
        session_manager.delete_session(message.from_user.id)

    await state.clear()
    await message.answer(
        "🔬 <b>PsycheOS Simulator v1.0</b>\n\n"
        "Выберите режим работы:",
        reply_markup=mode_keyboard(),
    )
    await state.set_state(SetupStates.choosing_mode)


# ═══════════════════════════════════════════════════════════════════════════
# ВЫБОР РЕЖИМА
# ═══════════════════════════════════════════════════════════════════════════

@router.callback_query(SetupStates.choosing_mode, F.data.startswith("mode:"))
async def on_mode_selected(callback: CallbackQuery, state: FSMContext):
    mode_value = callback.data.split(":")[1]
    await state.update_data(mode=mode_value)

    if mode_value == "TRAINING":
        # ОБУЧЕНИЕ → выбор кейса
        await callback.message.edit_text(
            "🎓 <b>Режим: Обучение</b>\n"
            "Сигнал супервизора + объяснение динамики\n\n"
            "Выберите кейс:",
            reply_markup=case_keyboard(),
        )
        await state.set_state(SetupStates.choosing_case)

    elif mode_value == "PRACTICE":
        # ТРЕНИРОВКА → загрузка данных
        await callback.message.edit_text(
            "🏋️ <b>Режим: Тренировка</b>\n"
            "Только сигнал супервизора (без объяснений)\n\n"
            "📎 Загрузите данные клиента.\n\n"
            "Отправьте текстом или файлом (.txt / .docx):\n"
            "— Screen-профиль\n"
            "— L0–L4 описания\n"
            "— Концептуализация (Layer A/B/C)\n\n"
            "<i>Или отправьте JSON с данными кейса.</i>"
        )
        await state.set_state(SetupStates.waiting_upload)

    await callback.answer()


# ═══════════════════════════════════════════════════════════════════════════
# ПОТОК ОБУЧЕНИЯ: кейс → цель → запуск
# ═══════════════════════════════════════════════════════════════════════════

@router.callback_query(SetupStates.choosing_case, F.data.startswith("case:"))
async def on_case_selected(callback: CallbackQuery, state: FSMContext):
    case_key = callback.data.split(":")[1]
    case = BUILTIN_CASES.get(case_key)

    if not case:
        await callback.answer("Кейс не найден", show_alert=True)
        return

    await state.update_data(case_key=case_key)

    crisis_icon = {"NONE": "⚪", "MODERATE": "🟡", "HIGH": "🔴"}

    await callback.message.edit_text(
        f"📋 <b>{case.case_name}</b>\n"
        f"👤 {case.client.gender}, {case.client.age} лет\n"
        f"⚠️ Кризис: {crisis_icon.get(case.crisis_flag.value, '')} {case.crisis_flag.value}\n"
        f"📊 Сложность: {case.difficulty}\n\n"
        f"Выберите цель сессии:",
        reply_markup=goal_keyboard(),
    )
    await state.set_state(SetupStates.choosing_goal)
    await callback.answer()


@router.callback_query(SetupStates.choosing_goal, F.data.startswith("goal:"))
async def on_goal_selected_training(callback: CallbackQuery, state: FSMContext):
    """Обучение: цель выбрана → запуск."""
    goal_value = callback.data.split(":")[1]

    try:
        goal = SessionGoal(goal_value)
    except ValueError:
        await callback.answer("Неизвестная цель", show_alert=True)
        return

    data = await state.get_data()
    case_key = data["case_key"]
    case = BUILTIN_CASES[case_key]
    mode = SessionMode.TRAINING

    await _launch_session(callback, state, case, goal, mode)


# ═══════════════════════════════════════════════════════════════════════════
# ПОТОК ТРЕНИРОВКИ: загрузка → кризис → цель → запуск
# ═══════════════════════════════════════════════════════════════════════════

@router.message(SetupStates.waiting_upload, F.text)
async def on_practice_data_text(message: Message, state: FSMContext):
    """Тренировка: получены данные текстом."""
    await state.update_data(custom_data=message.text)
    await message.answer(
        "✅ Данные получены.\n\n"
        "Выберите кризисный флаг:",
        reply_markup=crisis_keyboard(),
    )
    await state.set_state(SetupStates.choosing_crisis)


@router.message(SetupStates.waiting_upload, F.document)
async def on_practice_data_file(message: Message, state: FSMContext):
    """Тренировка: получены данные файлом."""
    file = message.document
    bot = message.bot

    try:
        file_info = await bot.get_file(file.file_id)
        file_bytes = await bot.download_file(file_info.file_path)
        content = file_bytes.read().decode("utf-8", errors="replace")
    except Exception as e:
        await message.answer(f"❌ Ошибка чтения файла: {e}\nПопробуйте отправить текстом.")
        return

    await state.update_data(custom_data=content)
    await message.answer(
        "✅ Файл получен и обработан.\n\n"
        "Выберите кризисный флаг:",
        reply_markup=crisis_keyboard(),
    )
    await state.set_state(SetupStates.choosing_crisis)


@router.callback_query(SetupStates.choosing_crisis, F.data.startswith("crisis:"))
async def on_crisis_selected(callback: CallbackQuery, state: FSMContext):
    crisis_value = callback.data.split(":")[1]
    await state.update_data(crisis=crisis_value)

    await callback.message.edit_text(
        f"⚠️ Кризис: {crisis_value}\n\n"
        f"Выберите цель сессии:",
        reply_markup=goal_keyboard(),
    )
    await state.set_state(SetupStates.choosing_goal_practice)
    await callback.answer()


@router.callback_query(SetupStates.choosing_goal_practice, F.data.startswith("goal:"))
async def on_goal_selected_practice(callback: CallbackQuery, state: FSMContext):
    """Тренировка: цель выбрана → запуск с пользовательскими данными."""
    goal_value = callback.data.split(":")[1]

    try:
        goal = SessionGoal(goal_value)
    except ValueError:
        await callback.answer("Неизвестная цель", show_alert=True)
        return

    data = await state.get_data()
    custom_data = data.get("custom_data", "")
    crisis_value = data.get("crisis", "NONE")
    mode = SessionMode.PRACTICE

    await _launch_session_custom(callback, state, custom_data, goal, mode, crisis_value)


# ═══════════════════════════════════════════════════════════════════════════
# ЗАПУСК СЕССИИ
# ═══════════════════════════════════════════════════════════════════════════

async def _launch_session(
    callback: CallbackQuery,
    state: FSMContext,
    case,
    goal: SessionGoal,
    mode: SessionMode,
):
    """Запуск сессии со встроенным кейсом (Обучение)."""
    await callback.message.edit_text("⏳ Инициализация симуляции...")

    session = session_manager.create_session(
        user_id=callback.from_user.id,
        case=case,
        goal=goal,
        mode=mode,
    )

    system_prompt = build_system_prompt(case, goal, mode)

    first_user_msg = (
        "Сессия начинается. Клиент входит в кабинет. "
        "Сгенерируй первую реплику клиента и начальный блок супервизора."
    )
    session_manager.add_message(callback.from_user.id, "user", first_user_msg)

    try:
        claude_response = await send_to_claude(
            system_prompt=system_prompt,
            messages=session.messages,
        )
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка Claude API:\n<code>{e}</code>")
        session_manager.delete_session(callback.from_user.id)
        await state.clear()
        return

    session_manager.add_message(callback.from_user.id, "assistant", claude_response)

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

    await callback.message.edit_text(formatted)
    await state.clear()
    await callback.answer()


async def _launch_session_custom(
    callback: CallbackQuery,
    state: FSMContext,
    custom_data: str,
    goal: SessionGoal,
    mode: SessionMode,
    crisis_value: str,
):
    """Запуск сессии с пользовательскими данными (Тренировка)."""
    from data.schemas import (
        BuiltinCase, ClientInfo, ScreenProfile, ContinuumScore,
        Layers, LayerDescription, Conceptualization, LayerA, LayerB,
        SystemCost, Target, CaseDynamics, CrisisFlag,
    )

    await callback.message.edit_text("⏳ Инициализация симуляции с вашими данными...")

    crisis = CrisisFlag(crisis_value)

    # Для пользовательских данных создаём минимальный кейс-обёртку
    # Claude сам извлечёт всю информацию из custom_data в system prompt
    baseline_L0 = {"NONE": 35, "MODERATE": 55, "HIGH": 78}[crisis_value]

    placeholder_case = BuiltinCase(
        case_id="CUSTOM",
        case_name="Пользовательский кейс",
        difficulty="CUSTOM",
        client=ClientInfo(id="CUSTOM", gender="не указан", age=0, presenting_complaints=["См. загруженные данные"]),
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

    session = session_manager.create_session(
        user_id=callback.from_user.id,
        case=placeholder_case,
        goal=goal,
        mode=mode,
    )

    # System prompt включает пользовательские данные целиком
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
    system_prompt += custom_block

    first_user_msg = (
        "Сессия начинается. Клиент входит в кабинет. "
        "Сгенерируй первую реплику клиента и начальный блок супервизора."
    )
    session_manager.add_message(callback.from_user.id, "user", first_user_msg)

    # Сохраняем кастомный промт в сессии для дальнейших итераций
    session_manager.store_system_prompt(callback.from_user.id, system_prompt)

    try:
        claude_response = await send_to_claude(
            system_prompt=system_prompt,
            messages=session.messages,
        )
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка Claude API:\n<code>{e}</code>")
        session_manager.delete_session(callback.from_user.id)
        await state.clear()
        return

    session_manager.add_message(callback.from_user.id, "assistant", claude_response)

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

    await callback.message.edit_text(formatted)
    await state.clear()
    await callback.answer()
