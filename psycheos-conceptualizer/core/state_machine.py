"""State Machine for PsycheOS Conceptualizer."""

from typing import Optional, Callable, Dict, Any
from .models import SessionState
from .enums import SessionStateEnum


class StateMachine:
    """Manages session state transitions and behaviors."""
    
    def __init__(self, session: SessionState):
        self.session = session
    
    def can_transition_to(self, target_state: SessionStateEnum) -> bool:
        """Check if can transition to target state."""
        valid_transitions = {
            SessionStateEnum.INIT: [SessionStateEnum.DATA_COLLECTION],
            SessionStateEnum.DATA_COLLECTION: [SessionStateEnum.ANALYSIS],
            SessionStateEnum.ANALYSIS: [SessionStateEnum.SOCRATIC_DIALOGUE],
            SessionStateEnum.SOCRATIC_DIALOGUE: [SessionStateEnum.OUTPUT_ASSEMBLY],
            SessionStateEnum.OUTPUT_ASSEMBLY: [SessionStateEnum.COMPLETE],
        }
        
        allowed = valid_transitions.get(self.session.state, [])
        return target_state in allowed
    
    def transition(self, target_state: SessionStateEnum) -> None:
        """Transition to new state with validation."""
        if not self.can_transition_to(target_state):
            raise ValueError(
                f"Invalid transition: {self.session.state} -> {target_state}"
            )
        
        self.session.transition_to(target_state)
    
    def get_current_state_prompt(self) -> str:
        """Get prompt filename for current state."""
        state_prompts = {
            SessionStateEnum.INIT: "init",
            SessionStateEnum.DATA_COLLECTION: "data_collection",
            SessionStateEnum.ANALYSIS: "analysis",
            SessionStateEnum.SOCRATIC_DIALOGUE: "dialogue",
            SessionStateEnum.OUTPUT_ASSEMBLY: "output",
        }
        
        return state_prompts.get(self.session.state, "base")
    
    def should_transition_automatically(self) -> Optional[SessionStateEnum]:
        """Check if should auto-transition to next state."""
        # INIT -> DATA_COLLECTION (автоматически после приветствия)
        if self.session.state == SessionStateEnum.INIT:
            return SessionStateEnum.DATA_COLLECTION
        
        # DATA_COLLECTION -> ANALYSIS (когда данные собраны)
        if self.session.state == SessionStateEnum.DATA_COLLECTION:
            if self.session.progress.data_collection_complete:
                return SessionStateEnum.ANALYSIS
        
        # ANALYSIS -> SOCRATIC_DIALOGUE (когда анализ завершен)
        if self.session.state == SessionStateEnum.ANALYSIS:
            if self.session.progress.analysis_complete:
                return SessionStateEnum.SOCRATIC_DIALOGUE
        
        # SOCRATIC_DIALOGUE -> OUTPUT_ASSEMBLY (когда готово)
        if self.session.state == SessionStateEnum.SOCRATIC_DIALOGUE:
            if self.session.can_proceed_to_output():
                # Не автоматически! Спросим специалиста
                return None
        
        return None
    
    def get_state_description(self) -> str:
        """Get human-readable state description."""
        descriptions = {
            SessionStateEnum.INIT: "🎯 Инициализация",
            SessionStateEnum.DATA_COLLECTION: "📊 Сбор данных",
            SessionStateEnum.ANALYSIS: "🔍 Анализ данных",
            SessionStateEnum.SOCRATIC_DIALOGUE: "💬 Диалог",
            SessionStateEnum.OUTPUT_ASSEMBLY: "📋 Формирование концептуализации",
            SessionStateEnum.COMPLETE: "✅ Завершено",
        }
        
        return descriptions.get(self.session.state, "Unknown")
    
    def get_state_instructions(self) -> str:
        """Get instructions for specialist in current state."""
        instructions = {
            SessionStateEnum.INIT: (
                "Добро пожаловать! Я помогу вам концептуализировать случай.\n\n"
                "Нажмите /start чтобы начать."
            ),
            SessionStateEnum.DATA_COLLECTION: (
                "📊 Этап сбора данных\n\n"
                "Предоставьте информацию о клиенте:\n"
                "- Screening данные (если есть)\n"
                "- Ваши наблюдения\n"
                "- Ключевые маркеры по слоям L0-L4\n\n"
                "Когда будете готовы, напишите 'готово'."
            ),
            SessionStateEnum.ANALYSIS: (
                "🔍 Анализ собранных данных\n\n"
                "Проверяю пропуски и потенциальные искажения...\n"
                "Один момент..."
            ),
            SessionStateEnum.SOCRATIC_DIALOGUE: (
                "💬 Сократовский диалог\n\n"
                "Буду задавать вопросы для формирования гипотез.\n"
                "Отвечайте естественно, думайте вслух.\n\n"
                "Я направлю процесс."
            ),
            SessionStateEnum.OUTPUT_ASSEMBLY: (
                "📋 Формирование концептуализации\n\n"
                "Собираю трёхслойный output:\n"
                "- Layer A: Техническая модель\n"
                "- Layer B: Мишени вмешательства\n"
                "- Layer C: Метафорический нарратив\n\n"
                "Это займет минуту..."
            ),
            SessionStateEnum.COMPLETE: (
                "✅ Концептуализация завершена!\n\n"
                "Используйте /new для новой сессии."
            ),
        }
        
        return instructions.get(self.session.state, "")


class StateValidator:
    """Validates state transitions and operations."""
    
    @staticmethod
    def validate_data_collection(session: SessionState) -> tuple[bool, str]:
        """Validate data collection is complete."""
        if not session.data_map:
            return False, "Нет данных. Предоставьте информацию о клиенте."
        
        if not session.data_map.specialist_observations:
            return False, "Добавьте ваши наблюдения."
        
        if len(session.data_map.specialist_observations) < 50:
            return False, "Наблюдения слишком краткие. Добавьте деталей."
        
        return True, "Данные собраны успешно."
    
    @staticmethod
    def validate_dialogue_ready(session: SessionState) -> tuple[bool, str]:
        """Validate ready for dialogue."""
        if session.state != SessionStateEnum.SOCRATIC_DIALOGUE:
            return False, "Сессия еще не в режиме диалога."
        
        return True, "Готово к диалогу."
    
    @staticmethod
    def validate_output_ready(session: SessionState) -> tuple[bool, str]:
        """Validate ready for output assembly."""
        if len(session.hypotheses) < 2:
            return False, f"Недостаточно гипотез ({len(session.hypotheses)}/2)."
        
        managerial = session.get_managerial_hypotheses()
        if len(managerial) < 1:
            return False, "Нет управленческой гипотезы."
        
        if session.has_blocking_flags():
            flags = session.get_blocking_red_flags()
            return False, f"Блокирующий флаг: {flags[0].description}"
        
        return True, "Готово к формированию концептуализации."


def get_state_machine(session: SessionState) -> StateMachine:
    """Factory function to create state machine."""
    return StateMachine(session)


def validate_state(session: SessionState, validation_type: str) -> tuple[bool, str]:
    """Convenience function for validation."""
    validator = StateValidator()
    
    if validation_type == "data_collection":
        return validator.validate_data_collection(session)
    elif validation_type == "dialogue":
        return validator.validate_dialogue_ready(session)
    elif validation_type == "output":
        return validator.validate_output_ready(session)
    
    return False, "Unknown validation type"


__all__ = [
    "StateMachine",
    "StateValidator",
    "get_state_machine",
    "validate_state",
]
