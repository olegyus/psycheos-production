"""
PsycheOS Pro Bot - For Specialists (Psychologists)

Commands:
/start - Welcome message and commands list
/register - Register as a specialist
/balance - Check token balance
/new_session - Create a new screening session
/transactions - View transaction history
"""

import asyncio
import sys
import traceback
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, ErrorEvent
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from app.config import settings
from app.logging_config import setup_logging, get_logger

# Initialize logging
setup_logging(log_level=settings.log_level, debug=settings.debug)
logger = get_logger("pro_bot")

# Initialize bot and dispatcher
bot = Bot(token=settings.telegram_pro_bot_token, default = DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()

# HTTP client for API calls
http_client: httpx.AsyncClient | None = None


async def get_client() -> httpx.AsyncClient:
    """Get or create HTTP client."""
    global http_client
    if http_client is None:
        http_client = httpx.AsyncClient(
            base_url=settings.backend_url,
            timeout=30.0,
        )
    return http_client


async def api_request(
    method: str,
    endpoint: str,
    json_data: dict | None = None,
) -> tuple[int, dict]:
    """
    Make API request to backend.
    
    Returns:
        Tuple of (status_code, response_json)
    """
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


# === Command Handlers ===

@router.message(CommandStart())
async def cmd_start(message: Message):
    """Handle /start command."""
    logger.info(
        "start_command",
        user_id=message.from_user.id,
        username=message.from_user.username,
    )
    
    welcome_text = """
👋 <b>Добро пожаловать в PsycheOS Pro!</b>

Это бот для специалистов-психологов, позволяющий проводить психологический скрининг клиентов до первой терапевтической сессии.

<b>Доступные команды:</b>
/register — Зарегистрироваться как специалист
/balance — Проверить баланс токенов
/new_session — Создать сессию скрининга для клиента
/transactions — История операций с токенами

<b>Как это работает:</b>
1. Зарегистрируйтесь командой /register
2. Создайте сессию командой /new_session
3. Отправьте ссылку клиенту
4. Получите результаты после завершения скрининга

🎁 При регистрации вы получите бесплатный токен для первой сессии!
"""
    await message.answer(welcome_text)


@router.message(Command("register"))
async def cmd_register(message: Message):
    """Handle /register command."""
    user = message.from_user
    logger.info(
        "register_command",
        user_id=user.id,
        username=user.username,
    )
    
    # Call API to register
    status_code, response = await api_request(
        "POST",
        "/api/v1/specialist/register",
        json_data={
            "telegram_id": user.id,
            "username": user.username,
            "name": user.full_name,
        }
    )
    
    if status_code == 201:
        balance = response.get("tokens_balance", 0)
        await message.answer(
            f"✅ <b>Регистрация успешна!</b>\n\n"
            f"👤 <b>Имя:</b> {user.full_name}\n"
            f"💰 <b>Баланс токенов:</b> {balance}\n\n"
            f"Используйте /new_session для создания сессии скрининга."
        )
    elif status_code == 409:
        await message.answer(
            "ℹ️ Вы уже зарегистрированы.\n"
            "Используйте /balance для проверки баланса."
        )
    else:
        error = response.get("detail", "Unknown error")
        await message.answer(f"❌ Ошибка регистрации: {error}")


@router.message(Command("balance"))
async def cmd_balance(message: Message):
    """Handle /balance command."""
    user_id = message.from_user.id
    logger.info("balance_command", user_id=user_id)
    
    status_code, response = await api_request(
        "GET",
        f"/api/v1/specialist/{user_id}/balance"
    )
    
    if status_code == 200:
        balance = response.get("tokens_balance", 0)
        spent = response.get("tokens_spent", 0)
        purchased = response.get("tokens_purchased", 0)
        
        await message.answer(
            f"💰 <b>Ваш баланс токенов</b>\n\n"
            f"📊 <b>Доступно:</b> {balance} токенов\n"
            f"📈 <b>Всего получено:</b> {purchased} токенов\n"
            f"📉 <b>Использовано:</b> {spent} токенов\n\n"
            f"💡 1 токен = 1 сессия скрининга"
        )
    elif status_code == 404:
        await message.answer(
            "❌ Вы не зарегистрированы.\n"
            "Используйте /register для регистрации."
        )
    else:
        error = response.get("detail", "Unknown error")
        await message.answer(f"❌ Ошибка: {error}")


@router.message(Command("new_session"))
async def cmd_new_session(message: Message):
    """Handle /new_session command."""
    user_id = message.from_user.id
    logger.info("new_session_command", user_id=user_id)
    
    # Check balance first
    balance_status, balance_response = await api_request(
        "GET",
        f"/api/v1/specialist/{user_id}/balance"
    )
    
    if balance_status == 404:
        await message.answer(
            "❌ Вы не зарегистрированы.\n"
            "Используйте /register для регистрации."
        )
        return
    
    balance = balance_response.get("tokens_balance", 0)
    if balance < 1:
        await message.answer(
            "❌ <b>Недостаточно токенов</b>\n\n"
            f"У вас {balance} токенов, требуется 1.\n\n"
            "💡 Для пополнения баланса обратитесь к администратору."
        )
        return
    
    # Create session
    await message.answer("⏳ Создаю сессию...")
    
    status_code, response = await api_request(
        "POST",
        "/api/v1/session/create",
        json_data={
            "specialist_telegram_id": user_id,
        }
    )
    
    if status_code == 201:
        session_id = response.get("session_id", "")
        deep_link = response.get("deep_link", "")
        expires_at = response.get("expires_at", "")
        
        await message.answer(
                    f"✅ <b>Сессия создана!</b>\n\n"
                    f"Скопируйте и отправьте клиенту следующее сообщение:"
                )
            
            # Сообщение для клиента (отдельным сообщением для удобного копирования)
        await message.answer(
            f"🧠 <b>Психологический скрининг PsycheOS</b>\n\n"
            f"Перед первой консультацией прошу Вас пройти короткий скрининг. "
            f"Это займёт около 10-15 минут и поможет мне лучше подготовиться к нашей встрече.\n\n"
            f"📋 <b>Важно:</b>\n"
            f"• Ваши ответы конфиденциальны\n"
            f"• Нет правильных или неправильных ответов\n"
            f"• Отвечайте так, как чувствуете\n\n"
            f"Нажимая на ссылку ниже, вы даёте согласие на обработку ваших ответов.\n\n"
            f"👉 <a href=\"{deep_link}\">Начать скрининг</a>\n\n"
            f"<i>ID: {session_id}\n"
            f"Действует до: {expires_at}</i>"
        )
    elif status_code == 402:
        await message.answer(
            "❌ <b>Недостаточно токенов</b>\n\n"
            "Для создания сессии требуется 1 токен."
        )
    else:
        error = response.get("detail", "Unknown error")
        await message.answer(f"❌ Ошибка: {error}")


@router.message(Command("transactions"))
async def cmd_transactions(message: Message):
    """Handle /transactions command."""
    user_id = message.from_user.id
    logger.info("transactions_command", user_id=user_id)
    
    status_code, response = await api_request(
        "GET",
        f"/api/v1/specialist/{user_id}/transactions?limit=10"
    )
    
    if status_code == 200:
        transactions = response.get("transactions", [])
        total = response.get("total", 0)
        
        if not transactions:
            await message.answer("📜 <b>История транзакций пуста</b>")
            return
        
        text = f"📜 <b>Последние транзакции</b> (всего: {total})\n\n"
        
        for tx in transactions:
            amount = tx.get("amount", 0)
            tx_type = tx.get("transaction_type", "")
            description = tx.get("description", "")
            created_at = tx.get("created_at", "")[:10]
            
            # Format amount
            amount_str = f"+{amount}" if amount > 0 else str(amount)
            emoji = "➕" if amount > 0 else "➖"
            
            text += f"{emoji} <b>{amount_str}</b> | {tx_type}\n"
            text += f"   📝 {description}\n"
            text += f"   📅 {created_at}\n\n"
        
        await message.answer(text)
    elif status_code == 404:
        await message.answer(
            "❌ Вы не зарегистрированы.\n"
            "Используйте /register для регистрации."
        )
    else:
        error = response.get("detail", "Unknown error")
        await message.answer(f"❌ Ошибка: {error}")

@router.message(Command("results"))
async def cmd_results(message: Message):
    """Handle /results command - send results as .txt file."""
    user_id = message.from_user.id
    
    # Parse session_id from command
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(
            "❌ Укажите ID сессии:\n"
            "/results <session_id>"
        )
        return
    
    session_id = parts[1]
    logger.info("results_command", user_id=user_id, session_id=session_id)
    
    await message.answer("⏳ Загружаю результаты...")
    
    status_code, response = await api_request(
        "GET",
        f"/api/v1/session/{session_id}/output"
    )
    
    if status_code == 200:
        screening_output = response.get("screening_output", {})
        interview_protocol = response.get("interview_protocol", {})
        
        # Format full report
        report_text = format_full_report(session_id, screening_output, interview_protocol)
        
        # Create and send file
        from io import BytesIO
        file_content = report_text.encode('utf-8')
        file = BytesIO(file_content)
        file.name = f"screening_report_{session_id[:8]}.txt"
        
        from aiogram.types import BufferedInputFile
        input_file = BufferedInputFile(file_content, filename=file.name)
        
        await message.answer_document(
            document=input_file,
            caption=f"📊 Результаты скрининга\n\nID сессии: {session_id[:8]}..."
        )
        
    elif status_code == 404:
        await message.answer("❌ Сессия не найдена или результаты ещё не готовы.")
    else:
        error = response.get("detail", "Unknown error")
        await message.answer(f"❌ Ошибка: {error}")


def format_full_report(session_id: str, screening_output: dict, interview_protocol: dict) -> str:
    """Format complete screening report as plain text."""
    lines = []
    lines.append("=" * 60)
    lines.append("РЕЗУЛЬТАТЫ ПСИХОЛОГИЧЕСКОГО СКРИНИНГА PsycheOS")
    lines.append("=" * 60)
    lines.append(f"\nID сессии: {session_id}")
    
    # Metadata
    metadata = screening_output.get("metadata", {})
    if metadata:
        lines.append(f"Экранов пройдено: {metadata.get('screens_completed', '?')}")
        lines.append(f"Качество данных: {metadata.get('data_quality', '?')}")
    
    lines.append("\n" + "-" * 60)
    lines.append("ПРОФИЛЬ ПО КОНТИНУУМАМ")
    lines.append("-" * 60)
    
    continuum_names = {
        "context": "Общий контекст",
        "economy_exploration": "Экономия ↔ Исследование",
        "protection_contact": "Защита ↔ Контакт",
        "retention_movement": "Удержание ↔ Движение",
        "survival_development": "Выживание ↔ Развитие",
    }
    
    continuum_profile = screening_output.get("continuum_profile", {})
    for key, name in continuum_names.items():
        data = continuum_profile.get(key, {})
        if data:
            position = data.get("position", "?")
            confidence = data.get("confidence", "?")
            note = data.get("interpretation_note", "")
            
            lines.append(f"\n{name}")
            lines.append(f"  Позиция: {position}/10")
            lines.append(f"  Уверенность: {confidence}")
            if note:
                lines.append(f"  Интерпретация: {note}")
    
    # Interview markers
    markers = screening_output.get("interview_markers", {})
    if markers:
        lines.append("\n" + "-" * 60)
        lines.append("МАРКЕРЫ ДЛЯ ИНТЕРВЬЮ")
        lines.append("-" * 60)
        
        tensions = markers.get("areas_of_tension", [])
        if tensions:
            lines.append("\nЗоны напряжения:")
            for t in tensions:
                lines.append(f"  • {t}")
        
        focus = markers.get("recommended_focus", "")
        if focus:
            lines.append(f"\nРекомендуемый фокус:\n  {focus}")
    
    # Interview Protocol
    lines.append("\n" + "=" * 60)
    lines.append("ПРОТОКОЛ ИНТЕРВЬЮ")
    lines.append("=" * 60)
    
    # General profile
    general = interview_protocol.get("general_profile", {})
    if general:
        summary = general.get("summary", "")
        if summary:
            lines.append(f"\nОбщий профиль:\n{summary}")
    
    # Working hypotheses
    hypotheses = interview_protocol.get("working_hypotheses", [])
    if hypotheses:
        lines.append("\nРабочие гипотезы:")
        for i, h in enumerate(hypotheses, 1):
            lines.append(f"  {i}. {h}")
    
    # Question directions
    questions = interview_protocol.get("question_directions", {})
    if questions:
        lines.append("\nНаправления вопросов:")
        
        question_labels = {
            "experience_questions": "Вопросы об опыте",
            "context_questions": "Контекстные вопросы",
            "change_questions": "Вопросы об изменениях",
            "contact_protection_questions": "Вопросы о контакте/защите",
            "resource_questions": "Вопросы о ресурсах",
        }
        
        for key, label in question_labels.items():
            q_list = questions.get(key, [])
            if q_list:
                lines.append(f"\n  {label}:")
                for q in q_list:
                    lines.append(f"    • {q}")
    
    # Recommended focus
    focus = interview_protocol.get("recommended_session_focus", "")
    if focus:
        lines.append(f"\nФокус первой сессии:\n{focus}")
    
    lines.append("\n" + "=" * 60)
    lines.append("Отчёт сгенерирован системой PsycheOS")
    lines.append("=" * 60)
    
    return "\n".join(lines)

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
                "❌ Произошла ошибка при обработке запроса.\n"
                "Пожалуйста, попробуйте позже."
            )
    except Exception:
        pass


# === Main ===

async def on_startup():
    """Startup actions."""
    logger.info("pro_bot_starting")
    
    # Set bot commands
    from aiogram.types import BotCommand
    commands = [
        BotCommand(command="start", description="Начать работу"),
        BotCommand(command="register", description="Зарегистрироваться"),
        BotCommand(command="balance", description="Проверить баланс"),
        BotCommand(command="new_session", description="Создать сессию"),
        BotCommand(command="results", description="Результаты сессии"),
        BotCommand(command="transactions", description="История транзакций"),
    ]
    await bot.set_my_commands(commands)
    
    logger.info("pro_bot_started")


async def on_shutdown():
    """Shutdown actions."""
    logger.info("pro_bot_shutting_down")
    
    global http_client
    if http_client:
        await http_client.aclose()
    
    await bot.session.close()
    logger.info("pro_bot_shutdown_complete")


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
