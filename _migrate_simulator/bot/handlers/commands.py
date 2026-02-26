"""Хендлеры команд v1.1: /end, /state, /help, /pause.

v1.1:
  - /end парсит TSI из отчёта Claude
  - Обновляет профиль специалиста
  - Генерирует .docx с iteration_log, TSI, CCI, профилем
"""

import logging
import os
import re

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, FSInputFile

from bot.keyboards.inline import confirm_end_keyboard
from core import session_manager
from core.claude_client import send_to_claude
from core.formatter import _escape_html
from core.report_generator import generate_report_docx
from data.cases import BUILTIN_CASES
from data.goals import GOAL_LABELS, MODE_LABELS
from data.schemas import TSIComponents, CCIComponents, compute_cci
from data.system_prompt import build_system_prompt

logger = logging.getLogger(__name__)

router = Router()


# ── /help ──────────────────────────────────────────────────────────────────

@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "🔬 <b>PsycheOS Simulator v1.1 — Команды</b>\n\n"
        "/start — Запуск / выбор нового кейса\n"
        "/end — Завершить сессию → аналитический отчёт (.docx)\n"
        "/state — Текущее состояние сессии\n"
        "/pause — Пауза\n"
        "/help — Эта справка\n\n"
        "<i>Во время сессии просто пишите текстом — "
        "это ваши реплики как специалиста.</i>"
    )


# ── /state ─────────────────────────────────────────────────────────────────

@router.message(Command("state"))
async def cmd_state(message: Message):
    session = session_manager.get_session(message.from_user.id)
    if not session:
        await message.answer("Нет активной сессии. /start для запуска.")
        return

    goal_label = GOAL_LABELS.get(session.session_goal, session.session_goal.value)
    mode_label = MODE_LABELS.get(session.mode.value, session.mode.value)

    greens = session.signal_log.count("🟢")
    yellows = session.signal_log.count("🟡")
    reds = session.signal_log.count("🔴")
    exchanges = len(session.iteration_log)

    # Последняя итерация
    last_info = ""
    if session.iteration_log:
        last = session.iteration_log[-1]
        last_info = (
            f"\n\n📈 <b>Последняя реплика:</b>\n"
            f"Layer: {last.active_layer_before} | "
            f"Match: {last.regulatory_match_score:.2f} | "
            f"Cascade: {last.cascade_probability:.2f}\n"
            f"Δtrust={last.delta.trust:+d} "
            f"Δtension={last.delta.tension_L0:+d} "
            f"Δuncertainty={last.delta.uncertainty:+d}"
        )

    await message.answer(
        f"📊 <b>Состояние сессии</b>\n\n"
        f"📋 Кейс: {_escape_html(session.case_name)}\n"
        f"🎯 Цель: {_escape_html(goal_label)}\n"
        f"📖 Режим: {_escape_html(mode_label)}\n"
        f"⚠️ Кризис: {session.crisis_flag.value}\n\n"
        f"🔄 FSM: <b>{session.fsm_state.value}</b>\n"
        f"💬 Реплик: {exchanges}\n"
        f"🟢 {greens}  🟡 {yellows}  🔴 {reds}\n"
        f"📈 Траектория: {' → '.join(session.fsm_log[-10:])}"
        f"{last_info}"
    )


# ── /end ───────────────────────────────────────────────────────────────────

@router.message(Command("end"))
async def cmd_end(message: Message):
    session = session_manager.get_session(message.from_user.id)
    if not session:
        await message.answer("Нет активной сессии.")
        return

    await message.answer(
        "Завершить сессию и получить аналитический отчёт?",
        reply_markup=confirm_end_keyboard(),
    )


@router.callback_query(F.data == "end:cancel")
async def on_end_cancel(callback: CallbackQuery):
    await callback.message.edit_text("Сессия продолжается. Пишите реплику.")
    await callback.answer()


@router.callback_query(F.data == "end:confirm")
async def on_end_confirm(callback: CallbackQuery):
    await callback.answer()  # Сразу, чтобы не было таймаута

    session = session_manager.get_session(callback.from_user.id)
    if not session:
        await callback.message.edit_text("Сессия уже завершена.")
        return

    await callback.message.edit_text("⏳ Формирование аналитического отчёта...")

    # Добавляем команду завершения
    session_manager.add_message(callback.from_user.id, "user", "/end")

    # Получаем system prompt
    system_prompt = _get_system_prompt(callback.from_user.id, session)

    try:
        report_text = await send_to_claude(
            system_prompt=system_prompt,
            messages=session.messages,
            max_tokens=4096,
        )
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка:\n<code>{e}</code>")
        return

    # ── Парсим TSI из отчёта Claude ────────────────────────────────────
    tsi = _parse_tsi_from_report(report_text)
    session.tsi = tsi

    # ── Получаем CCI ──────────────────────────────────────────────────
    cci = _get_cci(session.case_id)

    # ── Обновляем профиль специалиста ──────────────────────────────────
    specialist_profile = session_manager.update_profile_after_session(
        user_id=callback.from_user.id,
        session=session,
        tsi=tsi,
    )

    # ── Генерируем .docx ──────────────────────────────────────────────
    goal_label = GOAL_LABELS.get(session.session_goal, session.session_goal.value)
    mode_label = MODE_LABELS.get(session.mode.value, session.mode.value)

    try:
        docx_path = generate_report_docx(
            report_text=report_text,
            case_name=session.case_name,
            case_id=session.case_id,
            session_goal=goal_label,
            mode=mode_label,
            crisis_flag=session.crisis_flag.value,
            signal_log=session.signal_log,
            fsm_log=session.fsm_log,
            iteration_log=session.iteration_log,
            tsi=tsi,
            cci=cci,
            specialist_profile=specialist_profile,
        )
    except Exception as e:
        logger.error("Report generation failed: %s", e)
        await _send_text_fallback(callback, report_text, tsi)
        session_manager.close_session(callback.from_user.id)
        session_manager.delete_session(callback.from_user.id)
        return

    # ── Отправляем .docx ──────────────────────────────────────────────
    exchanges = len(session.iteration_log)
    greens = session.signal_log.count("🟢")
    yellows = session.signal_log.count("🟡")
    reds = session.signal_log.count("🔴")

    tsi_text = f"TSI: {tsi.tsi:.2f} ({tsi.interpretation})" if tsi else "TSI: н/д"
    cci_text = f"CCI: {cci.cci:.2f}" if cci else ""

    caption = (
        f"📋 <b>Аналитический отчёт v1.1</b>\n\n"
        f"Кейс: {_escape_html(session.case_name)}\n"
        f"Реплик: {exchanges} | "
        f"🟢{greens} 🟡{yellows} 🔴{reds}\n"
        f"📊 {tsi_text}"
    )
    if cci_text:
        caption += f" | {cci_text}"

    doc_file = FSInputFile(docx_path, filename=os.path.basename(docx_path))
    await callback.message.answer_document(document=doc_file, caption=caption)

    await callback.message.answer(
        "✅ Сессия завершена. Используйте /start для новой симуляции."
    )

    # Очистка
    session_manager.close_session(callback.from_user.id)
    session_manager.delete_session(callback.from_user.id)

    try:
        os.remove(docx_path)
    except OSError:
        pass


# ── /pause ─────────────────────────────────────────────────────────────────

@router.message(Command("pause"))
async def cmd_pause(message: Message):
    session = session_manager.get_session(message.from_user.id)
    if not session:
        await message.answer("Нет активной сессии.")
        return

    await message.answer(
        "⏸ <b>Пауза</b>\n\n"
        "Сессия приостановлена.\n"
        "Для продолжения просто напишите следующую реплику."
    )


# ═══════════════════════════════════════════════════════════════════════════
# TSI PARSING
# ═══════════════════════════════════════════════════════════════════════════

def _parse_tsi_from_report(report_text: str) -> TSIComponents | None:
    """Парсит TSI-компоненты из текста отчёта Claude."""
    try:
        def _extract(pattern: str, text: str) -> float:
            match = re.search(pattern, text)
            if match:
                return float(match.group(1))
            return 0.0

        r_match = _extract(r'R_match:\s*([\d.]+)', report_text)
        l_cons = _extract(r'L_consistency:\s*([\d.]+)', report_text)
        alliance = _extract(r'Alliance_score:\s*([\d.]+)', report_text)
        unc_mod = _extract(r'Uncertainty_modulation:\s*([\d.]+)', report_text)
        reactivity = _extract(r'Therapist_reactivity:\s*([\d.]+)', report_text)

        # Проверяем что хотя бы 3 компонента нашлись
        values = [r_match, l_cons, alliance, unc_mod, reactivity]
        if sum(1 for v in values if v > 0) < 3:
            logger.warning("TSI parsing: fewer than 3 components found")
            return None

        return TSIComponents(
            R_match=min(1.0, r_match),
            L_consistency=min(1.0, l_cons),
            Alliance_score=min(1.0, alliance),
            Uncertainty_modulation=min(1.0, unc_mod),
            Therapist_reactivity=min(1.0, reactivity),
        )
    except Exception as e:
        logger.error("TSI parsing failed: %s", e)
        return None


def _get_cci(case_id: str) -> CCIComponents | None:
    """Получает CCI для кейса."""
    case_map = {v.case_id: v for v in BUILTIN_CASES.values()}
    case = case_map.get(case_id)
    if case:
        return case.cci
    return None


def _get_system_prompt(user_id: int, session) -> str:
    custom = session_manager.get_system_prompt(user_id)
    if custom:
        return custom

    case_map = {v.case_id: k for k, v in BUILTIN_CASES.items()}
    case_key = case_map.get(session.case_id, "1")
    case = BUILTIN_CASES.get(case_key, list(BUILTIN_CASES.values())[0])
    return build_system_prompt(case, session.session_goal, session.mode)


# ═══════════════════════════════════════════════════════════════════════════
# FALLBACK
# ═══════════════════════════════════════════════════════════════════════════

async def _send_text_fallback(callback, report_text, tsi=None):
    report_escaped = _escape_html(report_text)
    header = "📋 <b>АНАЛИТИЧЕСКИЙ ОТЧЁТ</b>\n\n"
    if tsi:
        header += f"📊 TSI: {tsi.tsi:.2f} ({tsi.interpretation})\n\n"
    full_text = header + report_escaped

    chunks = _split_text(full_text, 4000)
    for chunk in chunks:
        await callback.message.answer(chunk)


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
