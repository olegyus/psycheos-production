"""Inline-клавиатуры для Telegram UI."""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from data.cases import BUILTIN_CASES
from data.goals import GOAL_LABELS, MODE_LABELS
from data.schemas import SessionGoal, CrisisFlag


# ── Выбор режима (ПЕРВЫЙ ШАГ) ─────────────────────────────────────────────

def mode_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора режима — первый экран."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🎓 Обучение — готовые кейсы",
            callback_data="mode:TRAINING",
        )],
        [InlineKeyboardButton(
            text="🏋️ Тренировка — свои данные",
            callback_data="mode:PRACTICE",
        )],
    ])


# ── Выбор кейса (только ОБУЧЕНИЕ) ─────────────────────────────────────────

def case_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора кейса (режим Обучение)."""
    buttons = []
    for key, case in BUILTIN_CASES.items():
        crisis_icon = {"NONE": "⚪", "MODERATE": "🟡", "HIGH": "🔴"}
        icon = crisis_icon.get(case.crisis_flag.value, "")
        label = f"{key}. {case.case_name} {icon} CCI:{case.cci.cci:.2f}"
        buttons.append([InlineKeyboardButton(
            text=label,
            callback_data=f"case:{key}",
        )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ── Выбор цели (оба режима) ───────────────────────────────────────────────

def goal_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора цели сессии."""
    buttons = []
    for goal, label in GOAL_LABELS.items():
        buttons.append([InlineKeyboardButton(
            text=label,
            callback_data=f"goal:{goal.value}",
        )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ── Выбор кризисного флага (только ТРЕНИРОВКА) ────────────────────────────

def crisis_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора кризисного флага (режим Тренировка)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚪ Нет кризиса", callback_data="crisis:NONE")],
        [InlineKeyboardButton(text="🟡 Умеренный", callback_data="crisis:MODERATE")],
        [InlineKeyboardButton(text="🔴 Высокий", callback_data="crisis:HIGH")],
    ])


# ── Подтверждение завершения ───────────────────────────────────────────────

def confirm_end_keyboard() -> InlineKeyboardMarkup:
    """Подтверждение завершения сессии."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, завершить", callback_data="end:confirm"),
            InlineKeyboardButton(text="❌ Продолжить", callback_data="end:cancel"),
        ],
    ])
