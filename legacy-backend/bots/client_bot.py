"""
PsycheOS Client Bot - For Clients (Screening)

This bot handles the screening process for clients.
Clients receive a deep link from their therapist and complete the screening here.
"""

import asyncio
import sys
import traceback
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import Message, CallbackQuery, ErrorEvent
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties


from app.config import settings
from app.logging_config import setup_logging, get_logger
from bots.keyboards import (
    build_start_screening_keyboard,
    build_continue_keyboard,
    build_info_keyboard,
    build_slider_keyboard,
    build_single_choice_keyboard,
    build_multi_choice_keyboard,
    parse_response_callback,
    parse_multi_callback,
    parse_multi_done_callback,
)

# Initialize logging
setup_logging(log_level=settings.log_level, debug=settings.debug)
logger = get_logger("client_bot")

# Initialize bot and dispatcher
bot = Bot(token=settings.telegram_client_bot_token, default = DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()

# HTTP client for API calls
http_client: httpx.AsyncClient | None = None


# === FSM States ===

class ScreeningStates(StatesGroup):
    """States for screening process."""
    waiting_for_response = State()
    screening_in_progress = State()


# === Helper Functions ===

async def get_client() -> httpx.AsyncClient:
    """Get or create HTTP client."""
    global http_client
    if http_client is None:
        http_client = httpx.AsyncClient(
            base_url=settings.backend_url,
            timeout=httpx.Timeout(120.0, connect=10.0),
        )
    return http_client


async def api_request(
    method: str,
    endpoint: str,
    json_data: dict | None = None,
) -> tuple[int, dict]:
    """Make API request to backend."""
    client = await get_client()
    
    try:
        if method.upper() == "GET":
            response = await client.get(endpoint)
        elif method.upper() == "POST":
            response = await client.post(endpoint, json=json_data)
        else:
            raise ValueError(f"Unsupported method: {method}")
        
        return response.status_code, response.json()
    
    except httpx.RequestError as e:
        logger.error(
            "api_request_error",
            exc_info=e,
            method=method,
            endpoint=endpoint,
        )
        return 500, {"detail": f"Connection error: {e}"}

async def notify_specialist(
    specialist_telegram_id: int,
    session_id: str,
    screens_completed: int,
    duration: int,
) -> None:
    """Send notification to specialist about completed screening."""
    try:
        # Use Pro Bot token to send message
        telegram_api_url = f"https://api.telegram.org/bot{settings.telegram_pro_bot_token}/sendMessage"
        
        message_text = (
            f"🎉 <b>Скрининг завершён!</b>\n\n"
            f"📋 ID сессии: <code>{session_id}</code>\n"
            f"📊 Вопросов: {screens_completed}\n"
            f"⏱ Время: {duration} мин.\n\n"
            f"Используйте команду:\n"
            f"/results {session_id}\n\n"
            f"для просмотра результатов."
        )
        
        async with httpx.AsyncClient() as client:
            await client.post(
                telegram_api_url,
                json={
                    "chat_id": specialist_telegram_id,
                    "text": message_text,
                    "parse_mode": "HTML",
                }
            )
        
        logger.info(
            "specialist_notified",
            specialist_telegram_id=specialist_telegram_id,
            session_id=session_id,
        )
    except Exception as e:
        logger.error(
            "specialist_notification_error",
            exc_info=e,
            specialist_telegram_id=specialist_telegram_id,
            session_id=session_id,
        )
        
async def show_screen(
    message_or_callback: Message | CallbackQuery,
    screen: dict,
    state: FSMContext,
) -> None:
    """Display a screen to the client with appropriate keyboard."""
    screen_id = screen.get("screen_id", "")
    screen_type = screen.get("screen_type", "slider")
    
    # Get stimulus
    stimulus = screen.get("stimulus", {})
    response_format = screen.get("response_format", {})
    format_type = response_format.get("type", screen_type)
    
    # Build message text based on screen type
    if format_type == "info":
        # Info screen — just text
        text = stimulus.get("text", "")
        keyboard = build_info_keyboard(screen_id)
    
    elif format_type == "slider":
        # Slider screen
        statement = stimulus.get("statement", "")
        question = stimulus.get("question", "")
        
        if statement:
            text = f"<b>{statement}</b>\n\n{question}"
        else:
            text = f"<b>{question}</b>"
        
        # Add scale anchors
        left_anchor = response_format.get("left_anchor", "0")
        right_anchor = response_format.get("right_anchor", "10")
        text += f"\n\n<i>0 — {left_anchor}</i>\n<i>10 — {right_anchor}</i>"
        
        keyboard = build_slider_keyboard(screen_id)
    
    elif format_type == "single_choice":
        # Single choice — options in text, letter buttons
        situation = stimulus.get("situation", "")
        question = stimulus.get("question", "")
        options = response_format.get("options", [])
        
        if situation:
            text = f"{situation}\n\n<b>{question}</b>"
        else:
            text = f"<b>{question}</b>"
        
        # Add options as A), B), C), D)
        letters = ["A", "B", "C", "D", "E", "F", "G", "H"]
        options_text = "\n".join(
            f"<b>{letters[i]})</b> {opt}" for i, opt in enumerate(options) if i < len(letters)
        )
        text += f"\n\n{options_text}"
        
        keyboard = build_single_choice_keyboard(screen_id, options)
    
    elif format_type == "multi_choice":
        # Multi choice — options in text with letters
        question = stimulus.get("question", "")
        options = response_format.get("options", [])
        
        text = f"<b>{question}</b>\n<i>(можно выбрать несколько)</i>"
        
        # Add options as A), B), C), etc.
        letters = ["A", "B", "C", "D", "E", "F", "G", "H"]
        options_text = "\n".join(
            f"<b>{letters[i]})</b> {opt}" for i, opt in enumerate(options) if i < len(letters)
        )
        text += f"\n\n{options_text}"
        
        # Initialize empty selection
        await state.update_data(multi_selected=[])
        keyboard = build_multi_choice_keyboard(screen_id, options, selected=[])
    
    else:
        # Fallback
        text = stimulus.get("question", stimulus.get("text", "Вопрос"))
        keyboard = build_slider_keyboard(screen_id)
    
    # Save current screen to state
    await state.update_data(current_screen_id=screen_id, current_screen=screen)
    
    # Send message
    if isinstance(message_or_callback, CallbackQuery):
        await message_or_callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await message_or_callback.answer(text, reply_markup=keyboard, parse_mode="HTML")


async def handle_screening_response(
    callback: CallbackQuery,
    session_id: str,
    screen_id: str,
    value: str,
    state: FSMContext,
) -> None:
    """Handle client's response to a screen."""
    logger.info(
        "screening_response",
        session_id=session_id,
        screen_id=screen_id,
        value=value,
    )
    
    # Get current screen from state to determine response type
    data = await state.get_data()
    current_screen = data.get("current_screen", {})
    response_format = current_screen.get("response_format", {})
    format_type = response_format.get("type", "")
    
    # Handle info screen (just "next")
    if format_type == "info" or value == "next":
        response_value = "next"
    else:
        # Convert value
        try:
            int_value = int(value)
            
            # For single_choice, convert index to option text
            if format_type == "single_choice":
                options = response_format.get("options", [])
                if 0 <= int_value < len(options):
                    response_value = options[int_value]
                else:
                    response_value = value
            else:
                # Slider — keep as int
                response_value = int_value
        except ValueError:
            response_value = value
    
    # Send response to API
    status_code, response = await api_request(
        "POST",
        f"/api/v1/session/{session_id}/response",
        json_data={
            "screen_id": screen_id,
            "response_value": response_value,
        }
    )
    
    if status_code != 200:
        error = response.get("detail", "Unknown error")
        logger.error(
            "response_submit_error",
            session_id=session_id,
            status_code=status_code,
            error=error,
        )
        await callback.message.edit_text(
            f"❌ Ошибка: {error}\n\n"
            "Попробуйте ещё раз или обратитесь к вашему терапевту."
        )
        return
    
    next_action = response.get("next_action", "next_screen")
    
    if next_action == "finalize":
        await finalize_screening(callback, session_id, state)
    else:
        await show_next_screen(callback, session_id, state)

@router.callback_query(F.data.startswith("multi:"), ScreeningStates.screening_in_progress)
async def handle_multi_toggle(callback: CallbackQuery, state: FSMContext):
    """Handle multi-choice option toggle."""
    await callback.answer()
    
    parsed = parse_multi_callback(callback.data)
    if not parsed:
        return
    
    screen_id, option_idx = parsed
    
    # Get current selection
    data = await state.get_data()
    selected = data.get("multi_selected", [])
    current_screen = data.get("current_screen", {})
    options = current_screen.get("response_format", {}).get("options", [])
    
    # Toggle selection
    if option_idx in selected:
        selected.remove(option_idx)
    else:
        selected.append(option_idx)
    
    await state.update_data(multi_selected=selected)
    
    # Update keyboard to show new selection
    keyboard = build_multi_choice_keyboard(screen_id, options, selected)
    await callback.message.edit_reply_markup(reply_markup=keyboard)


@router.callback_query(F.data.startswith("multi_done:"), ScreeningStates.screening_in_progress)
async def handle_multi_done(callback: CallbackQuery, state: FSMContext):
    """Handle multi-choice completion."""
    parsed = parse_multi_done_callback(callback.data)
    if not parsed:
        await callback.answer("Ошибка")
        return
    
    screen_id = parsed
    
    # Get selection
    data = await state.get_data()
    selected = data.get("multi_selected", [])
    current_screen = data.get("current_screen", {})
    session_id = data.get("session_id")
    options = current_screen.get("response_format", {}).get("options", [])
    
    if not selected:
        await callback.answer("Выберите хотя бы один вариант", show_alert=True)
        return
    
    await callback.answer()
    
    # Convert indices to option texts
    selected_texts = [options[i] for i in selected if i < len(options)]
    
    logger.info(
        "multi_choice_response",
        session_id=session_id,
        screen_id=screen_id,
        selected=selected_texts,
    )
    
    # Send response to API (as comma-separated string)
    response_value = "; ".join(selected_texts)
    
    status_code, response = await api_request(
        "POST",
        f"/api/v1/session/{session_id}/response",
        json_data={
            "screen_id": screen_id,
            "response_value": response_value,
        }
    )
    
    if status_code != 200:
        error = response.get("detail", "Unknown error")
        logger.error("response_error", session_id=session_id, error=error)
        await callback.message.edit_text(f"❌ Ошибка: {error}")
        return
    
    # Get next action
    next_action = response.get("next_action", "next_screen")
    
    if next_action == "finalize":
        await finalize_screening(callback, session_id, state)
    else:
        await show_next_screen(callback, session_id, state)

async def show_next_screen(
    callback: CallbackQuery,
    session_id: str,
    state: FSMContext,
) -> None:
    """Get and show the next screen."""
    await callback.message.edit_text("⏳ Загружаю следующий вопрос...")
    
    status_code, response = await api_request(
        "GET",
        f"/api/v1/session/{session_id}/next_screen"
    )
    
    if status_code != 200:
        error = response.get("detail", "Unknown error")
        await callback.message.edit_text(f"❌ Ошибка: {error}")
        return
    
    action = response.get("action", "next_screen")
    progress = response.get("progress", {})
    
    if action == "finalize":
        await finalize_screening(callback, session_id, state)
        return
    
    screen = response.get("screen")
    if not screen:
        await finalize_screening(callback, session_id, state)
        return
    
    # Show progress
    completed = progress.get("screens_completed", 0)
    remaining = progress.get("estimated_remaining", 0)
    
    # Show the screen
    await show_screen(callback, screen, state)


async def finalize_screening(
    callback: CallbackQuery,
    session_id: str,
    state: FSMContext,
) -> None:
    """Finalize the screening session."""
    await callback.message.edit_text("⏳ Завершаю скрининг, подождите...")
    
    status_code, response = await api_request(
        "POST",
        f"/api/v1/session/{session_id}/finalize"
    )
    
    if status_code != 200:
        error = response.get("detail", "Unknown error")
        logger.error(
            "finalize_error",
            session_id=session_id,
            status_code=status_code,
            error=error,
        )
        await callback.message.edit_text(
            "✅ <b>Скрининг завершён!</b>\n\n"
            "Спасибо за ваше время!\n"
            "Вы обсудите результаты со специалистом на сессии. 🙏"
        )
        await state.clear()
        return
    
    # Show simple message to client
    await callback.message.edit_text(
        "✅ <b>Скрининг завершён!</b>\n\n"
        "Спасибо за ваше время!\n"
        "Вы обсудите результаты со специалистом на сессии. 🙏"
    )
    
    # Notify specialist
    specialist_telegram_id = response.get("specialist_telegram_id")
    if specialist_telegram_id:
        await notify_specialist(
            specialist_telegram_id=specialist_telegram_id,
            session_id=session_id,
            screens_completed=response.get("screens_completed", 0),
            duration=response.get("duration_minutes", 0),
        )
    
    # Clear state
    await state.clear()
    
    logger.info(
        "screening_completed",
        session_id=session_id,
        screens_completed=response.get("screens_completed", 0),
    )


# === Command Handlers ===

@router.message(CommandStart(deep_link=True))
async def cmd_start_with_session(message: Message, command: CommandObject, state: FSMContext):
    """Handle /start with session_id (deep link)."""
    session_id = command.args
    
    if not session_id:
        await message.answer(
            "❌ Неверная ссылка.\n"
            "Пожалуйста, получите корректную ссылку от вашего терапевта."
        )
        return
    
    user_id = message.from_user.id
    logger.info(
        "deep_link_start",
        session_id=session_id,
        user_id=user_id,
    )
    
    # Start session via API
    status_code, response = await api_request(
        "POST",
        f"/api/v1/session/{session_id}/start",
        json_data={"client_telegram_id": user_id} if user_id else None,
    )
    
    if status_code == 404:
        await message.answer(
            "❌ Сессия не найдена.\n"
            "Возможно, ссылка неверна или устарела.\n"
            "Пожалуйста, запросите новую ссылку у вашего терапевта."
        )
        return
    
    if status_code == 410:
        await message.answer(
            "⏰ Срок действия сессии истёк.\n"
            "Пожалуйста, запросите новую ссылку у вашего терапевта."
        )
        return
    
    if status_code == 409:
        await message.answer(
            "ℹ️ Эта сессия уже была завершена.\n"
            "Результаты переданы вашему терапевту."
        )
        return
    
    if status_code != 200:
        error = response.get("detail", "Unknown error")
        await message.answer(f"❌ Ошибка: {error}")
        return
    
    # Save session_id to state
    await state.update_data(session_id=session_id)
    await state.set_state(ScreeningStates.screening_in_progress)
    
    # Welcome message
    await message.answer(
        "👋 <b>Добро пожаловать в PsycheOS!</b>\n\n"
        "Вы начинаете психологический скрининг.\n\n"
        "📝 Вам будет предложено несколько вопросов.\n"
        "⏱ Это займёт около 10-15 минут.\n"
        "✨ Отвечайте честно — правильных или неправильных ответов нет.\n\n"
        "Готовы начать?",
        reply_markup=build_start_screening_keyboard()
    )


@router.message(CommandStart())
async def cmd_start_no_session(message: Message):
    """Handle /start without session_id."""
    await message.answer(
        "👋 <b>Добро пожаловать в PsycheOS!</b>\n\n"
        "Этот бот предназначен для прохождения психологического скрининга.\n\n"
        "📎 Чтобы начать, вам нужна ссылка от вашего терапевта.\n"
        "Ссылка выглядит примерно так:\n"
        "<code>https://t.me/PsycheOS_Client_bot?start=...</code>\n\n"
        "Если у вас есть ссылка, просто нажмите на неё."
    )


@router.message(Command("continue"))
async def cmd_continue(message: Message, state: FSMContext):
    """Handle /continue command to resume screening."""
    data = await state.get_data()
    session_id = data.get("session_id")
    
    if not session_id:
        await message.answer(
            "❌ Нет активной сессии.\n"
            "Используйте ссылку от вашего терапевта для начала скрининга."
        )
        return
    
    await message.answer("⏳ Загружаю следующий вопрос...")
    
    # Get next screen
    status_code, response = await api_request(
        "GET",
        f"/api/v1/session/{session_id}/next_screen"
    )
    
    if status_code != 200:
        error = response.get("detail", "Unknown error")
        await message.answer(f"❌ Ошибка: {error}")
        return
    
    screen = response.get("screen")
    action = response.get("action", "next_screen")
    
    if action == "finalize" or not screen:
        # Fake callback for finalize
        class FakeCallback:
            def __init__(self, msg):
                self.message = msg
        await finalize_screening(FakeCallback(message), session_id, state)
        return
    
    await show_screen(message, screen, state)


# === Callback Handlers ===

@router.callback_query(F.data == "start_screening")
async def callback_start_screening(callback: CallbackQuery, state: FSMContext):
    """Handle Start Screening button."""
    await callback.answer()
    
    data = await state.get_data()
    session_id = data.get("session_id")
    
    if not session_id:
        await callback.message.edit_text("❌ Ошибка: сессия не найдена.")
        return
    
    # Get first screen
    status_code, response = await api_request(
        "GET",
        f"/api/v1/session/{session_id}/next_screen"
    )
    
    if status_code != 200:
        error = response.get("detail", "Unknown error")
        await callback.message.edit_text(f"❌ Ошибка: {error}")
        return
    
    screen = response.get("screen")
    if not screen:
        await callback.message.edit_text("❌ Ошибка: не удалось загрузить первый вопрос.")
        return
    
    await show_screen(callback, screen, state)


@router.callback_query(F.data.startswith("response:"))
async def callback_response(callback: CallbackQuery, state: FSMContext):
    """Handle response callback (slider or forced choice)."""
    await callback.answer()
    
    try:
        _, screen_id, value = parse_response_callback(callback.data)
    except ValueError as e:
            parsed = parse_response_callback(callback.data)
            if not parsed:
                 logger.error("invalid_callback_data", callback_data=callback.data)
                 await callback.message.edit_text("❌ Ошибка обработки ответа.")
                 return
    
            screen_id, value = parsed
    
    data = await state.get_data()
    session_id = data.get("session_id")
    
    if not session_id:
        await callback.message.edit_text(
            "❌ Сессия не найдена.\n"
            "Пожалуйста, используйте ссылку от терапевта для начала."
        )
        return
    
    await handle_screening_response(callback, session_id, screen_id, value, state)


@router.callback_query(F.data == "continue")
async def callback_continue(callback: CallbackQuery, state: FSMContext):
    """Handle Continue button."""
    await callback.answer()
    
    data = await state.get_data()
    session_id = data.get("session_id")
    
    if not session_id:
        await callback.message.edit_text("❌ Сессия не найдена.")
        return
    
    await show_next_screen(callback, session_id, state)


# === Error Handler ===

@router.error()
async def error_handler(event: ErrorEvent):
    """Handle all bot errors with full traceback."""
    exception = event.exception
    update = event.update
    
    # Get full traceback
    tb = traceback.format_exception(type(exception), exception, exception.__traceback__)
    tb_str = "".join(tb)
    
    logger.error(
        "bot_error",
        exc_info=exception,
        traceback=tb_str,
        update_type=type(update).__name__ if update else None,
        error_type=type(exception).__name__,
        error_message=str(exception),
    )
    
    # Try to notify user
    try:
        if update and update.message:
            await update.message.answer(
                "❌ Произошла ошибка.\n"
                "Попробуйте использовать команду /continue для продолжения\n"
                "или обратитесь к вашему терапевту."
            )
        elif update and update.callback_query:
            await update.callback_query.message.answer(
                "❌ Произошла ошибка.\n"
                "Попробуйте использовать команду /continue для продолжения."
            )
    except Exception:
        pass


# === Main ===

async def on_startup():
    """Startup actions."""
    logger.info("client_bot_starting")
    
    # Set bot commands
    from aiogram.types import BotCommand
    commands = [
        BotCommand(command="start", description="Начать скрининг"),
        BotCommand(command="continue", description="Продолжить скрининг"),
    ]
    await bot.set_my_commands(commands)
    
    logger.info("client_bot_started")


async def on_shutdown():
    """Shutdown actions."""
    logger.info("client_bot_shutting_down")
    
    global http_client
    if http_client:
        await http_client.aclose()
    
    await bot.session.close()
    logger.info("client_bot_shutdown_complete")


async def main():
    """Main function."""
    # Register router
    dp.include_router(router)
    
    # Register startup/shutdown
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    # Start polling
    try:
        logger.info("starting_polling")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except KeyboardInterrupt:
        logger.info("keyboard_interrupt_received")
    finally:
        await on_shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
