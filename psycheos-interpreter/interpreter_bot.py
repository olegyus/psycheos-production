#!/usr/bin/env python3
"""
PsycheOS Interpreter Bot
Main Telegram bot file
"""
import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile

import config
from orchestrator import Orchestrator
from datetime import datetime, timezone
import base64

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize bot and dispatcher
bot = Bot(token=config.INTERPRETER_BOT_TOKEN)
dp = Dispatcher()

# Initialize orchestrator
orchestrator = Orchestrator()

# Store active sessions (user_id -> session_id)
active_sessions = {}


@dp.message(Command("start"))
async def cmd_start(message: Message):
    """Handle /start command."""
    await message.answer(
        "🧠 <b>PsycheOS Interpreter Bot</b>\n\n"
        "Я помогаю интерпретировать символический материал:\n"
        "• Сны\n"
        "• Рисунки\n"
        "• Проективные образы\n\n"
        "Отправьте мне описание сна или загрузите изображение рисунка.\n\n"
        "<i>Используйте /new для начала новой сессии</i>",
        parse_mode="HTML"
    )


@dp.message(Command("new"))
async def cmd_new(message: Message):
    """Start new interpretation session."""
    user_id = message.from_user.id
    
    # Create new session
    session = orchestrator.create_session(user_id)
    active_sessions[user_id] = session.session_id
    
    await message.answer(
        f"✓ Новая сессия начата\n"
        f"ID: <code>{session.session_id}</code>\n\n"
        f"Опишите символический материал, который вы хотите интерпретировать.",
        parse_mode="HTML"
    )
    
    logger.info(f"New session started for user {user_id}: {session.session_id}")


@dp.message(Command("help"))
async def cmd_help(message: Message):
    """Show help."""
    await message.answer(
        "<b>Доступные команды:</b>\n\n"
        "/start - Начать работу с ботом\n"
        "/new - Начать новую сессию интерпретации\n"
        "/status - Статус текущей сессии\n"
        "/help - Эта справка\n\n"
        "<b>Как использовать:</b>\n\n"
        "1. Отправьте /new для начала новой сессии\n"
        "2. Опишите сон или загрузите рисунок\n"
        "3. Отвечайте на уточняющие вопросы\n"
        "4. Получите результат в виде файла\n\n"
        "<i>Бот использует метод осторожной интерпретации с явными ограничениями.</i>",
        parse_mode="HTML"
    )


@dp.message(Command("status"))
async def cmd_status(message: Message):
    """Show session status."""
    user_id = message.from_user.id
    
    if user_id not in active_sessions:
        await message.answer("У вас нет активной сессии. Используйте /new для начала.")
        return
    
    session_id = active_sessions[user_id]
    session = orchestrator.load_session(session_id)
    
    if not session:
        await message.answer("Сессия не найдена. Используйте /new для начала новой.")
        del active_sessions[user_id]
        return
    
    await message.answer(
        f"<b>Статус сессии</b>\n\n"
        f"ID: <code>{session.session_id}</code>\n"
        f"Состояние: {session.state}\n"
        f"Режим: {session.mode}\n"
        f"Уточнений: {session.iteration_count}/{config.MAX_CLARIFICATION_ITERATIONS}\n"
        f"Создана: {session.created_at}\n"
        f"Обновлена: {session.updated_at}",
        parse_mode="HTML"
    )


@dp.message(F.photo)
async def handle_photo(message: Message):
    """Handle photo uploads (drawings)."""
    user_id = message.from_user.id
    
    # Ensure session exists
    if user_id not in active_sessions:
        session = orchestrator.create_session(user_id)
        active_sessions[user_id] = session.session_id
        await message.answer("✓ Автоматически создана новая сессия.")
    
    session_id = active_sessions[user_id]
    session = orchestrator.load_session(session_id)
    
    if not session:
        await message.answer("Ошибка: сессия не найдена. Используйте /new")
        return
    
    await message.answer("📸 Изображение получено. Анализирую рисунок...")
    await bot.send_chat_action(message.chat.id, "typing")
    
    try:
        # Get photo
        photo = message.photo[-1]  # Get largest photo
        
        # Download photo
        file = await bot.get_file(photo.file_id)
        photo_bytes = await bot.download_file(file.file_path)
        
        # Convert to base64
        import base64
        photo_base64 = base64.b64encode(photo_bytes.read()).decode('utf-8')
        
        # Analyze with Claude Vision
        from anthropic import Anthropic
        client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
        
        vision_response = client.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=2000,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": photo_base64
                            }
                        },
                        {
                            "type": "text",
                            "text": """Опишите этот рисунок для психологической интерпретации.

Укажите:
- Основные элементы и объекты
- Композицию (расположение, размеры)
- Цвета и их использование
- Линии (чёткие, размытые, прерывистые)
- Пространство (заполненность, пустоты)
- Общее впечатление

Описание должно быть феноменологическим, без интерпретаций."""
                        }
                    ]
                }
            ]
        )
        
        description = vision_response.content[0].text
        
        # Add to session material
        session.accumulated_material.append({
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'content': f"[Рисунок]\n\n{description}",
            'type': 'image_analysis'
        })
        session.material_type = 'drawing'
        session.save()
        
        # Send description to user and proceed
        await message.answer(
            f"✓ Рисунок проанализирован:\n\n{description}\n\n"
            f"Хотите добавить что-то от себя или сразу перейти к интерпретации?\n\n"
            f"Напишите дополнительный контекст или 'продолжить' для интерпретации."
        )
        
    except Exception as e:
        logger.error(f"Error processing photo: {e}", exc_info=True)
        await message.answer(
            f"❌ Ошибка при обработке изображения: {str(e)}\n\n"
            f"Попробуйте описать рисунок текстом или загрузите другое фото."
        )


@dp.message(F.text)
async def handle_text(message: Message):
    """Handle text messages."""
    user_id = message.from_user.id
    text = message.text
    
    # Skip commands
    if text.startswith('/'):
        return
    
    # Ensure session exists
    if user_id not in active_sessions:
        session = orchestrator.create_session(user_id)
        active_sessions[user_id] = session.session_id
        await message.answer(
            "✓ Автоматически создана новая сессия.\n"
            "Обрабатываю ваше сообщение..."
        )
    
    session_id = active_sessions[user_id]
    session = orchestrator.load_session(session_id)
    
    if not session:
        await message.answer("Ошибка: сессия не найдена. Используйте /new")
        return
    
    # Show typing indicator
    await bot.send_chat_action(message.chat.id, "typing")
    
    try:
        # Process message through orchestrator
        logger.info(f"Processing message for user {user_id} in state {session.state}")
        
        result = orchestrator.process_message(session, text)
        
        # Check if result is a file path
        if isinstance(result, str) and result.endswith('.txt'):
            # Send file
            output_file = Path(result)
            
            if output_file.exists():
                await message.answer("✅ Интерпретация завершена!")
                
                # Send TXT file
                document = FSInputFile(output_file)
                await message.answer_document(
                    document,
                    caption="📄 Результаты интерпретации"
                )
                
                # Also send JSON if exists
                json_file = output_file.with_suffix('.json')
                if json_file.exists():
                    json_doc = FSInputFile(json_file)
                    await message.answer_document(
                        json_doc,
                        caption="📋 Структурированные данные (JSON)"
                    )
                
                # Clear session
                del active_sessions[user_id]
                
                await message.answer(
                    "Сессия завершена.\n"
                    "Используйте /new для начала новой интерпретации."
                )
            else:
                await message.answer(f"Ошибка: файл не найден - {result}")
        else:
            # Send text response
            await message.answer(result)
    
    except Exception as e:
        logger.error(f"Error processing message: {e}", exc_info=True)
        await message.answer(
            f"❌ Произошла ошибка при обработке:\n<code>{str(e)}</code>\n\n"
            f"Попробуйте начать новую сессию с /new",
            parse_mode="HTML"
        )


async def main():
    """Main bot loop."""
    logger.info("Starting PsycheOS Interpreter Bot...")
    logger.info(f"Model: {config.ANTHROPIC_MODEL}")
    logger.info(f"Sessions dir: {config.SESSIONS_DIR}")
    logger.info(f"Outputs dir: {config.OUTPUTS_DIR}")
    
    # Start polling
    await dp.start_polling(bot)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot crashed: {e}", exc_info=True)
