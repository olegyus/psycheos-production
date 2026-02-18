"""Question types for Socratic Dialogue."""

from typing import Optional
from core.models import SessionState, Hypothesis
from core.enums import QuestionType, HypothesisType, PsycheLevelEnum


class QuestionGenerator:
    """Generates questions of different types based on context."""
    
    def __init__(self, session: SessionState, hypothesis: Optional[Hypothesis] = None):
        self.session = session
        self.hypothesis = hypothesis
        self.active_hypotheses = session.get_active_hypotheses()
    
    def generate_level_check(self) -> str:
        """Generate LEVEL_CHECK question."""
        if self.hypothesis:
            hyp = self.hypothesis
            layers_str = ", ".join([l.value for l in hyp.levels])
            
            if PsycheLevelEnum.L4 in hyp.levels or PsycheLevelEnum.L3 in hyp.levels:
                return (
                    f"Вы отнесли это к {layers_str}. "
                    "Что конкретно указывает, что это именно этот уровень, "
                    "а не автоматическая реакция (L1) или выученный паттерн (L2)?"
                )
            
            if len(hyp.levels) > 2:
                return (
                    f"Эта гипотеза охватывает {len(hyp.levels)} слоя ({layers_str}). "
                    "Можем ли мы определить ОСНОВНОЙ слой, где напряжение максимально?"
                )
            
            return (
                f"Какие данные подтверждают отнесение к {layers_str}? "
                "Могло ли это быть на другом уровне?"
            )
        
        return "Какой слой показывает максимальное напряжение?"
    
    def generate_function_check(self) -> str:
        """Generate FUNCTION_CHECK question."""
        if self.hypothesis and self.hypothesis.function:
            return f"Вы определили функцию как: '{self.hypothesis.function}'. Что сломается если система прекратит этот паттерн?"
        
        return "Какую задачу решает система, поддерживая этот паттерн?"
    
    def generate_dynamics_check(self) -> str:
        """Generate DYNAMICS_CHECK question."""
        return "Что поддерживает этот паттерн во времени?"
    
    def generate_alternatives_check(self) -> str:
        """Generate ALTERNATIVES_CHECK question."""
        return "Какое альтернативное объяснение могло бы учесть те же данные?"
    
    def generate_control_check(self) -> str:
        """Generate CONTROL_CHECK question."""
        managerial_count = len(self.session.get_managerial_hypotheses())
        
        if managerial_count == 0:
            return "Где эта система может быть реально затронута? Что может измениться?"
        
        return "Кто реальный агент изменения? Какова последовательность?"
    
    def generate_question(self, question_type: QuestionType) -> str:
        """Generate question of specified type."""
        generators = {
            QuestionType.LEVEL_CHECK: self.generate_level_check,
            QuestionType.FUNCTION_CHECK: self.generate_function_check,
            QuestionType.DYNAMICS_CHECK: self.generate_dynamics_check,
            QuestionType.ALTERNATIVES_CHECK: self.generate_alternatives_check,
            QuestionType.CONTROL_CHECK: self.generate_control_check,
        }
        
        generator = generators.get(question_type)
        if generator:
            return generator()
        
        return "Можете ли вы подробнее рассказать?"


def generate_question(
    session: SessionState,
    question_type: QuestionType,
    hypothesis: Optional[Hypothesis] = None
) -> str:
    """Convenience function to generate question."""
    generator = QuestionGenerator(session, hypothesis)
    return generator.generate_question(question_type)


__all__ = [
    "QuestionGenerator",
    "generate_question",
]
