"""PsycheOS Conceptualizer Telegram Bot."""

import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from config import init_config
from core import (
    SessionState,
    init_storage,
    get_storage,
    SessionStateEnum,
    Hypothesis,
    HypothesisType,
    PsycheLevelEnum,
    ConfidenceLevel,
)
from decision_policy import select_next_question, should_continue_dialogue
from output import assemble_output

# Initialize configuration
config = init_config()

# Initialize storage
storage = init_storage(
    host=config.redis_host,
    port=config.redis_port,
    db=config.redis_db,
    ttl=config.session_ttl
)

# Setup logging
logger = logging.getLogger(__name__)


# ========== HYPOTHESIS EXTRACTION ==========

def extract_hypothesis_from_response(message: str, session: SessionState) -> Hypothesis:
    """Extract hypothesis from specialist's response."""
    
    message_lower = message.lower()
    
    # Detect hypothesis type from keywords
    managerial_keywords = [
        "можно", "нужно", "стоит", "начать с", "сначала",
        "рекомендую", "предлагаю", "вмешательство", "воздействие",
        "изменить", "работать с", "фокус на", "приоритет"
    ]
    
    dynamic_keywords = [
        "поддерживает", "сохраняется", "закрепляется", "подкрепляется",
        "петля", "цикл", "механизм", "динамика", "со временем"
    ]
    
    functional_keywords = [
        "функция", "защищает", "предотвращает", "служит для",
        "помогает", "позволяет", "задача", "цель"
    ]
    
    # Detect layer from keywords
    layer_keywords = {
        PsycheLevelEnum.L0: ["l0", "энергия", "истощение", "выживание", "сон", "тело", "физически"],
        PsycheLevelEnum.L1: ["l1", "автоматически", "рефлекс", "привычка", "без сознания"],
        PsycheLevelEnum.L2: ["l2", "сознательно", "выбор", "решение", "контроль"],
        PsycheLevelEnum.L3: ["l3", "социально", "роль", "отношения", "другие люди"],
        PsycheLevelEnum.L4: ["l4", "смысл", "идентичность", "ценности", "кто я", "должен"],
    }
    
    # Determine type
    if any(kw in message_lower for kw in managerial_keywords):
        hyp_type = HypothesisType.MANAGERIAL
    elif any(kw in message_lower for kw in dynamic_keywords):
        hyp_type = HypothesisType.DYNAMIC
    elif any(kw in message_lower for kw in functional_keywords):
        hyp_type = HypothesisType.FUNCTIONAL
    else:
        hyp_type = HypothesisType.STRUCTURAL
    
    # Determine layer
    detected_layers = []
    for layer, keywords in layer_keywords.items():
        if any(kw in message_lower for kw in keywords):
            detected_layers.append(layer)
    
    if not detected_layers:
        detected_layers = [PsycheLevelEnum.L0]  # Default
    
    # Create hypothesis
    hypothesis = Hypothesis(
        id=f"hyp_{session.progress.hypotheses_added + 1:03d}",
        type=hyp_type,
        levels=detected_layers,
        formulation=message[:300],  # Truncate
        confidence=ConfidenceLevel.WORKING
    )
    
    return hypothesis


def is_clarification_request(message: str) -> bool:
    """Check if message is a clarification request."""
    clarification_keywords = [
        "что значит", "уточните", "поясните", "не понял",
        "непонятно", "объясните", "что имеется в виду",
        "?", "как это", "что это означает"
    ]
    
    message_lower = message.lower()
    return any(kw in message_lower for kw in clarification_keywords)


# ========== HANDLERS ==========

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    user_id = update.effective_user.id
    session_id = f"session_{user_id}"
    
    # Create new session
    session = SessionState(
        session_id=session_id,
        specialist_id=str(user_id)
    )
    
    # Save to storage
    storage.save_session(session)
    
    welcome_message = (
        "🎯 **PsycheOS Conceptualizer**\n\n"
        "Я помогу вам концептуализировать случай через структурированный диалог.\n\n"
        "**Процесс:**\n"
        "1️⃣ Сбор данных о клиенте\n"
        "2️⃣ Анализ пропусков\n"
        "3️⃣ Сократовский диалог\n"
        "4️⃣ Трёхслойная концептуализация\n\n"
        "**Готовы начать?**\n"
        "Отправьте информацию о клиенте или напишите 'начать'."
    )
    
    await update.message.reply_text(welcome_message, parse_mode="Markdown")
    logger.info(f"New session started: {session_id}")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command."""
    user_id = update.effective_user.id
    session_id = f"session_{user_id}"
    
    session = storage.load_session(session_id)
    
    if not session:
        await update.message.reply_text(
            "У вас нет активной сессии. Используйте /start для начала."
        )
        return
    
    hypotheses_count = len(session.get_active_hypotheses())
    managerial_count = len(session.get_managerial_hypotheses())
    
    # Show hypothesis types
    type_counts = {}
    for hyp in session.get_active_hypotheses():
        type_counts[hyp.type.value] = type_counts.get(hyp.type.value, 0) + 1
    
    status_msg = (
        f"📊 **Статус сессии**\n\n"
        f"Состояние: {session.state.value}\n"
        f"Диалог: {session.progress.dialogue_turns} вопросов\n\n"
        f"**Гипотезы: {hypotheses_count}**\n"
    )
    
    for hyp_type, count in type_counts.items():
        status_msg += f"  • {hyp_type}: {count}\n"
    
    if session.can_proceed_to_output():
        status_msg += "\n✅ Готово к формированию концептуализации!"
    else:
        if managerial_count == 0:
            status_msg += "\n⚠️ Нужна управленческая гипотеза"
    
    await update.message.reply_text(status_msg, parse_mode="Markdown")


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /reset command."""
    user_id = update.effective_user.id
    session_id = f"session_{user_id}"
    
    storage.delete_session(session_id)
    
    await update.message.reply_text(
        "🔄 Сессия сброшена. Используйте /start для новой сессии."
    )
    logger.info(f"Session reset: {session_id}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    help_text = (
        "🆘 **Справка**\n\n"
        "**Команды:**\n"
        "/start - Начать новую сессию\n"
        "/status - Проверить статус\n"
        "/reset - Сбросить сессию\n"
        "/help - Показать справку\n\n"
        "**Как отвечать на вопросы:**\n"
        "• Думайте вслух\n"
        "• Упоминайте конкретные слои (L0-L4)\n"
        "• Для управленческих гипотез используйте:\n"
        "  'можно', 'нужно', 'стоит начать с'\n\n"
        "**Пример ответа:**\n"
        "_'Думаю, стоит начать с L0 - стабилизировать сон и снизить нагрузку, "
        "потому что система в режиме выживания'_"
    )
    
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle regular messages."""
    user_id = update.effective_user.id
    session_id = f"session_{user_id}"
    user_message = update.message.text
    
    session = storage.load_session(session_id)
    
    if not session:
        await update.message.reply_text(
            "Используйте /start чтобы начать новую сессию."
        )
        return
    
    try:
        if session.state == SessionStateEnum.INIT:
            await handle_init_state(update, session, user_message)
        
        elif session.state == SessionStateEnum.DATA_COLLECTION:
            await handle_data_collection(update, session, user_message)
        
        elif session.state == SessionStateEnum.SOCRATIC_DIALOGUE:
            await handle_dialogue(update, session, user_message)
        
        elif session.state == SessionStateEnum.OUTPUT_ASSEMBLY:
            await handle_output_assembly(update, session)
        
        elif session.state == SessionStateEnum.COMPLETE:
            await update.message.reply_text(
                "Сессия завершена. Используйте /start для новой сессии."
            )
        
        storage.save_session(session)
        
    except Exception as e:
        logger.error(f"Error handling message: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ Произошла ошибка: {str(e)}\n\nИспользуйте /reset для сброса."
        )


async def handle_init_state(update: Update, session: SessionState, message: str) -> None:
    """Handle INIT state."""
    session.transition_to(SessionStateEnum.DATA_COLLECTION)
    
    await update.message.reply_text(
        "📊 **Этап 1: Сбор данных**\n\n"
        "Пожалуйста, предоставьте информацию о клиенте:\n"
        "- Основные жалобы\n"
        "- Наблюдения по слоям (L0-L4)\n"
        "- Ключевые маркеры\n\n"
        "Напишите 'готово' когда закончите."
    )


async def handle_data_collection(update: Update, session: SessionState, message: str) -> None:
    """Handle DATA_COLLECTION state."""
    
    if not session.data_map:
        from core.models import DataMap
        session.data_map = DataMap()
        session.data_map.specialist_observations = message
    else:
        session.data_map.specialist_observations += "\n" + message
    
    if "готов" in message.lower() and len(session.data_map.specialist_observations) > 50:
        session.progress.data_collection_complete = True
        session.transition_to(SessionStateEnum.ANALYSIS)
        
        await update.message.reply_text(
            "✅ Данные собраны.\n\n"
            "🔍 Анализирую...\n"
            "Один момент..."
        )
        
        session.transition_to(SessionStateEnum.SOCRATIC_DIALOGUE)
        
        selection = select_next_question(session)
        
        await update.message.reply_text(
            "💬 **Начинаем диалог**\n\n"
            f"❓ {selection.question_text}\n\n"
            "_Совет: упоминайте конкретные слои (L0-L4) и используйте "
            "'можно', 'нужно' для управленческих предложений._"
        )
        
        session.progress.increment_dialogue_turns()
    else:
        await update.message.reply_text(
            "Принято. Продолжайте или напишите 'готово'."
        )


async def handle_dialogue(update: Update, session: SessionState, message: str) -> None:
    """Handle SOCRATIC_DIALOGUE state."""
    
    # Check if this is a clarification request
    if is_clarification_request(message):
        await update.message.reply_text(
            "Давайте конкретизирую:\n\n"
            "**Где система может быть затронута?** означает:\n"
            "• На каком слое (L0-L4) можно влиять?\n"
            "• Что можно изменить без коллапса системы?\n"
            "• Какая минимальная коррекция даст эффект?\n\n"
            "**Пример ответа:**\n"
            "_'Думаю, стоит начать с L0 - нужно стабилизировать сон, "
            "потому что истощение блокирует все остальное'_\n\n"
            "Попробуйте ответить конкретно."
        )
        return
    
    # Extract hypothesis from response
    if len(message) > 30:  # Meaningful response
        hypothesis = extract_hypothesis_from_response(message, session)
        session.add_hypothesis(hypothesis)
        
        logger.info(f"Added hypothesis: {hypothesis.type.value} on {[l.value for l in hypothesis.levels]}")
    
    # Check if should continue
    should_continue, reason = should_continue_dialogue(session)
    
    if not should_continue:
        await update.message.reply_text(
            f"📋 {reason}\n\n"
            "Формирую концептуализацию..."
        )
        
        session.transition_to(SessionStateEnum.OUTPUT_ASSEMBLY)
        await handle_output_assembly(update, session)
        return
    
    # Get next question
    selection = select_next_question(session)
    
    # Add helpful hint based on question type
    hint = ""
    if selection.question_type.value == "control_check":
        hint = "\n\n_💡 Упомяните что 'можно', 'нужно', 'стоит начать с' для управленческой гипотезы_"
    
    await update.message.reply_text(
        f"💬 **Вопрос {session.progress.dialogue_turns + 1}**\n\n"
        f"❓ {selection.question_text}{hint}"
    )
    
    session.progress.increment_dialogue_turns()


async def handle_output_assembly(update: Update, session: SessionState) -> None:
    """Handle OUTPUT_ASSEMBLY state."""
    
    try:
        output = assemble_output(session)
        
        # Send Layer A
        layer_a_msg = (
            "📊 **LAYER A: Концептуальная модель**\n\n"
            f"**Ведущая гипотеза:**\n{output.layer_a.leading_formulation}\n\n"
            f"**Доминирующий слой:** {output.layer_a.dominant_layer.value}\n\n"
            f"**Конфигурация:**\n{output.layer_a.configuration_summary}\n\n"
            f"**Цена системы:**\n{output.layer_a.system_cost}"
        )
        await update.message.reply_text(layer_a_msg)
        
        # Send Layer B
        layer_b_msg = "🎯 **LAYER B: Мишени вмешательства**\n\n"
        for target in output.layer_b.targets:
            layer_b_msg += f"**{target.priority}. {target.layer}**\n"
            layer_b_msg += f"{target.direction}\n\n"
        
        layer_b_msg += f"**Последовательность:**\n{output.layer_b.sequencing_notes}"
        await update.message.reply_text(layer_b_msg)
        
        # Send Layer C
        layer_c_msg = (
            "🎭 **LAYER C: Метафорический нарратив**\n\n"
            f"**Метафора:** _{output.layer_c.core_metaphor}_\n\n"
            f"{output.layer_c.narrative}\n\n"
            f"**Направление изменения:**\n{output.layer_c.direction_of_change}"
        )
        await update.message.reply_text(layer_c_msg)
        
        session.transition_to(SessionStateEnum.COMPLETE)
        
        await update.message.reply_text(
            "✅ **Концептуализация завершена!**\n\n"
            "Используйте /start для новой сессии."
        )
        
    except Exception as e:
        logger.error(f"Error assembling output: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ Ошибка формирования концептуализации: {str(e)}"
        )


# ========== MAIN ==========

def main() -> None:
    """Start the bot."""
    
    application = Application.builder().token(config.telegram_bot_token).build()
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("reset", reset_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("Starting PsycheOS Conceptualizer Bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
