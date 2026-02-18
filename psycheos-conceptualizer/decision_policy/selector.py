"""Decision Policy Selector - Main question selection algorithm."""

from typing import Optional, Tuple
from dataclasses import dataclass
from core.models import SessionState, Hypothesis
from core.enums import QuestionType, HypothesisType
from .priorities import Priority, PriorityChecker
from .question_types import QuestionGenerator


@dataclass
class QuestionSelection:
    """Result of question selection process."""
    
    question_text: str
    question_type: QuestionType
    priority: Priority
    priority_reason: str
    context: Optional[str] = None


class DecisionPolicySelector:
    """Main selector for Decision Policy."""
    
    def __init__(self, session: SessionState):
        self.session = session
        self.priority_checker = PriorityChecker(session)
    
    def select_next_question(self) -> QuestionSelection:
        """Select the next question to ask based on current state."""
        
        # Step 1: Determine priority
        priority, priority_reason = self.priority_checker.check_priority()
        
        # Step 2: Select question type based on priority
        question_type = self._select_question_type(priority)
        
        # Step 3: Identify target hypothesis if relevant
        target_hypothesis = self._identify_target_hypothesis(priority, question_type)
        
        # Step 4: Generate question
        generator = QuestionGenerator(self.session, target_hypothesis)
        question_text = generator.generate_question(question_type)
        
        # Step 5: Add context
        context = self._generate_context(priority)
        
        return QuestionSelection(
            question_text=question_text,
            question_type=question_type,
            priority=priority,
            priority_reason=priority_reason,
            context=context
        )
    
    def _select_question_type(self, priority: Priority) -> QuestionType:
        """Select question type based on priority."""
        
        # Priority 1 (CRITICAL): No managerial → CONTROL_CHECK
        if priority == Priority.CRITICAL:
            return QuestionType.CONTROL_CHECK
        
        # Priority 2 (HIGH): Dominant without alternatives → ALTERNATIVES_CHECK
        elif priority == Priority.HIGH:
            return QuestionType.ALTERNATIVES_CHECK
        
        # Priority 3 (MEDIUM): Structural issues → LEVEL_CHECK or FUNCTION_CHECK
        elif priority == Priority.MEDIUM:
            active_hyps = self.session.get_active_hypotheses()
            
            # If many hypotheses but unclear structure → LEVEL_CHECK
            if len(active_hyps) >= 5:
                structural = [h for h in active_hyps if h.type == HypothesisType.STRUCTURAL]
                if len(structural) == 0:
                    return QuestionType.LEVEL_CHECK
            
            # Default for structural issues
            return QuestionType.FUNCTION_CHECK
        
        # Priority 4 (LOW): Refinement → DYNAMICS_CHECK
        elif priority == Priority.LOW:
            return QuestionType.DYNAMICS_CHECK
        
        # Priority NONE: General exploration
        else:
            turns = self.session.progress.dialogue_turns
            
            if turns < 3:
                return QuestionType.FUNCTION_CHECK
            elif turns < 7:
                return QuestionType.LEVEL_CHECK if turns % 2 == 0 else QuestionType.FUNCTION_CHECK
            else:
                return QuestionType.DYNAMICS_CHECK
    
    def _identify_target_hypothesis(
        self, 
        priority: Priority, 
        question_type: QuestionType
    ) -> Optional[Hypothesis]:
        """Identify specific hypothesis to target with question."""
        
        active_hyps = self.session.get_active_hypotheses()
        
        if not active_hyps:
            return None
        
        # For ALTERNATIVES_CHECK: target dominant hypothesis
        if question_type == QuestionType.ALTERNATIVES_CHECK:
            dominant = [h for h in active_hyps if h.confidence == "dominant"]
            if dominant:
                return dominant[0]
        
        # For FUNCTION_CHECK: target structural without function
        if question_type == QuestionType.FUNCTION_CHECK:
            structural = [h for h in active_hyps if h.type == HypothesisType.STRUCTURAL]
            if structural:
                no_function = [h for h in structural if not h.function]
                if no_function:
                    return no_function[0]
                return structural[0]
        
        # Default: return most recent hypothesis
        if active_hyps:
            return active_hyps[-1]
        
        return None
    
    def _generate_context(self, priority: Priority) -> Optional[str]:
        """Generate context explanation for why this question is being asked."""
        
        contexts = {
            Priority.CRITICAL: "Критический вопрос для определения точки управления.",
            Priority.HIGH: "Тестируем гипотезу против альтернатив.",
            Priority.MEDIUM: "Организуем гипотезы по архитектуре.",
            Priority.LOW: "Уточняем понимание динамики.",
        }
        
        return contexts.get(priority)
    
    def should_continue_dialogue(self) -> Tuple[bool, str]:
        """Determine if dialogue should continue or move to output."""
        
        # Check turn limit
        max_turns = 20
        current_turns = self.session.progress.dialogue_turns
        
        if current_turns >= max_turns:
            return False, f"Достигнут лимит ({max_turns} вопросов)"
        
        # Check if ready for output
        if self.session.can_proceed_to_output():
            return False, "Минимальная модель достигнута - готово к формированию концептуализации"
        
        # Check for blocking red flags
        if self.session.has_blocking_flags():
            blocking = self.session.get_blocking_red_flags()
            return False, f"Блокировано флагом: {blocking[0].description}"
        
        return True, "Модель неполная - продолжаем диалог"


def select_next_question(session: SessionState) -> QuestionSelection:
    """Convenience function to select next question."""
    selector = DecisionPolicySelector(session)
    return selector.select_next_question()


def should_continue_dialogue(session: SessionState) -> Tuple[bool, str]:
    """Check if dialogue should continue."""
    selector = DecisionPolicySelector(session)
    return selector.should_continue_dialogue()


__all__ = [
    "QuestionSelection",
    "DecisionPolicySelector",
    "select_next_question",
    "should_continue_dialogue",
]
