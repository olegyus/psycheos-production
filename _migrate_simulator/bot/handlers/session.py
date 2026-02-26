"""Хендлер основного диалога v1.1 — реплики специалиста + iteration log."""

import logging

from aiogram import Router, F
from aiogram.types import Message

from core import session_manager
from core.claude_client import send_to_claude
from core.formatter import (
    parse_claude_response, format_for_telegram, build_iteration_log, _escape_html,
)
from data.cases import BUILTIN_CASES
from data.system_prompt import build_system_prompt
from data.schemas import FSMState
from core.config import settings

logger = logging.getLogger(__name__)

router = Router()


@router.message(F.text & ~F.text.startswith("/"))
async def handle_specialist_message(message: Message):
    """Обработка реплики специалиста."""

    session = session_manager.get_session(message.from_user.id)

    if not session:
        await message.answer(
            "Нет активной сессии. Используйте /start для запуска."
        )
        return

    specialist_text = message.text

    # Добавляем реплику специалиста
    session_manager.add_message(message.from_user.id, "user", specialist_text)

    # Ограничиваем историю
    if len(session.messages) > settings.max_session_history:
        session.messages = (
            session.messages[:1] +
            session.messages[-(settings.max_session_history - 1):]
        )

    # Получаем system prompt
    system_prompt = _get_system_prompt(message.from_user.id, session)

    # Typing indicator
    await message.bot.send_chat_action(
        chat_id=message.chat.id, action="typing"
    )

    try:
        claude_response = await send_to_claude(
            system_prompt=system_prompt,
            messages=session.messages,
        )
    except Exception as e:
        logger.error("Claude error for user %d: %s", message.from_user.id, e)
        session.messages.pop()
        await message.answer(f"❌ Ошибка при обращении к Claude:\n<code>{e}</code>")
        return

    # Сохраняем ответ
    session_manager.add_message(message.from_user.id, "assistant", claude_response)

    # Парсим структурированный ответ v1.1
    parsed = parse_claude_response(claude_response)

    # v1.0: сигнал в простой лог
    if parsed.signal:
        session_manager.add_signal(message.from_user.id, parsed.signal)

    # v1.1: структурированный iteration log
    replica_id = session_manager.get_next_replica_id(message.from_user.id)
    iteration = build_iteration_log(
        parsed=parsed,
        replica_id=replica_id,
        specialist_input=specialist_text,
    )
    session_manager.add_iteration(message.from_user.id, iteration)

    # FSM tracking
    if parsed.fsm_state:
        session.fsm_log.append(parsed.fsm_state)
        for fs in FSMState:
            if fs.value == parsed.fsm_state:
                session.fsm_state = fs
                break

    # Форматируем и отправляем
    formatted = format_for_telegram(parsed)

    if len(formatted) > 4000:
        client_msg = f"🗣 <b>Клиент:</b>\n{_escape_html(parsed.client_text)}"
        await message.answer(client_msg)
        if parsed.supervisor_block:
            sup_msg = f"{'─' * 30}\n{_escape_html(parsed.supervisor_block)}"
            await message.answer(sup_msg)
    else:
        await message.answer(formatted)


def _get_system_prompt(user_id: int, session) -> str:
    custom = session_manager.get_system_prompt(user_id)
    if custom:
        return custom

    case_map = {v.case_id: k for k, v in BUILTIN_CASES.items()}
    case_key = case_map.get(session.case_id, "1")
    case = BUILTIN_CASES.get(case_key, list(BUILTIN_CASES.values())[0])
    return build_system_prompt(case, session.session_goal, session.mode)
