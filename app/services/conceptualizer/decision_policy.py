"""Decision policy: priority checking + question generation + dialogue control."""
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional, Tuple

from .enums import ConfidenceLevel, HypothesisType, PsycheLevelEnum, QuestionType
from .models import Hypothesis, SessionState


# ── Priority ──────────────────────────────────────────────────────────────────

class Priority(IntEnum):
    CRITICAL = 1  # No managerial hypothesis
    HIGH = 2      # Dominant without alternatives
    MEDIUM = 3    # Structural issues
    LOW = 4       # Refinement
    NONE = 5      # General exploration


class PriorityChecker:
    def __init__(self, session: SessionState):
        self.session = session
        self.active = session.get_active_hypotheses()
        self.managerial = session.get_managerial_hypotheses()

    def check_priority(self) -> Tuple[Priority, str]:
        for check in (
            self._check_no_managerial,
            self._check_dominant_without_alternatives,
            self._check_structural_issues,
            self._check_refinement_needed,
        ):
            p, r = check()
            if p != Priority.NONE:
                return p, r
        return Priority.NONE, "No specific priority — general exploration"

    def _check_no_managerial(self) -> Tuple[Priority, str]:
        s = sum(1 for h in self.active if h.type == HypothesisType.STRUCTURAL)
        f = sum(1 for h in self.active if h.type == HypothesisType.FUNCTIONAL)
        d = sum(1 for h in self.active if h.type == HypothesisType.DYNAMIC)
        m = len(self.managerial)

        if (s > 0 or f > 0 or d > 0) and m == 0:
            return Priority.CRITICAL, (
                f"Have understanding ({s}S+{f}F+{d}D) but NO managerial hypothesis."
            )
        if len(self.active) >= 3 and m == 0:
            return Priority.CRITICAL, (
                f"Model has {len(self.active)} hypotheses but no management point."
            )
        return Priority.NONE, ""

    def _check_dominant_without_alternatives(self) -> Tuple[Priority, str]:
        dominant = [h for h in self.active if h.confidence == ConfidenceLevel.DOMINANT]
        for dom in dominant:
            same_type = [h for h in self.active if h.type == dom.type and h.id != dom.id]
            if not same_type:
                return Priority.HIGH, (
                    f"Dominant {dom.type.value} hypothesis has no alternatives."
                )
        return Priority.NONE, ""

    def _check_structural_issues(self) -> Tuple[Priority, str]:
        if not self.active:
            return Priority.NONE, ""

        if len(self.active) >= 5:
            structural = [h for h in self.active if h.type == HypothesisType.STRUCTURAL]
            if not structural:
                return Priority.MEDIUM, (
                    f"Have {len(self.active)} hypotheses but no structural hypothesis."
                )
            confident = [
                h for h in self.active
                if h.confidence in [ConfidenceLevel.WORKING, ConfidenceLevel.DOMINANT]
            ]
            if not confident:
                return Priority.MEDIUM, (
                    f"Have {len(self.active)} hypotheses but all weak/conditional."
                )

        if len(self.active) >= 3:
            all_layers: set = set()
            for hyp in self.active:
                all_layers.update(hyp.levels)
            if len(all_layers) == 1:
                layer = list(all_layers)[0]
                return Priority.MEDIUM, (
                    f"All hypotheses on {layer.value}. Need multi-layer understanding."
                )

        return Priority.NONE, ""

    def _check_refinement_needed(self) -> Tuple[Priority, str]:
        m = len(self.managerial)
        total = len(self.active)
        if m == 0 or total < 2 or total > 6:
            return Priority.NONE, ""
        types_present = {h.type for h in self.active}
        if len(types_present) >= 2 and m >= 1:
            return Priority.LOW, (
                f"Model nearly complete: {total} hypotheses including {m} managerial."
            )
        return Priority.NONE, ""


# ── Question generation ───────────────────────────────────────────────────────

class QuestionGenerator:
    def __init__(self, session: SessionState, hypothesis: Optional[Hypothesis] = None):
        self.session = session
        self.hypothesis = hypothesis

    def generate_level_check(self) -> str:
        if self.hypothesis:
            hyp = self.hypothesis
            layers_str = ", ".join(l.value for l in hyp.levels)
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
        if self.hypothesis and self.hypothesis.function:
            return (
                f"Вы определили функцию как: '{self.hypothesis.function}'. "
                "Что сломается если система прекратит этот паттерн?"
            )
        return "Какую задачу решает система, поддерживая этот паттерн?"

    def generate_dynamics_check(self) -> str:
        return "Что поддерживает этот паттерн во времени?"

    def generate_alternatives_check(self) -> str:
        return "Какое альтернативное объяснение могло бы учесть те же данные?"

    def generate_control_check(self) -> str:
        if not self.session.get_managerial_hypotheses():
            return "Где эта система может быть реально затронута? Что может измениться?"
        return "Кто реальный агент изменения? Какова последовательность?"

    def generate_question(self, question_type: QuestionType) -> str:
        generators = {
            QuestionType.LEVEL_CHECK: self.generate_level_check,
            QuestionType.FUNCTION_CHECK: self.generate_function_check,
            QuestionType.DYNAMICS_CHECK: self.generate_dynamics_check,
            QuestionType.ALTERNATIVES_CHECK: self.generate_alternatives_check,
            QuestionType.CONTROL_CHECK: self.generate_control_check,
        }
        gen = generators.get(question_type)
        return gen() if gen else "Можете ли вы подробнее рассказать?"


# ── Selector ──────────────────────────────────────────────────────────────────

@dataclass
class QuestionSelection:
    question_text: str
    question_type: QuestionType
    priority: Priority
    priority_reason: str
    context: Optional[str] = None


class DecisionPolicySelector:
    def __init__(self, session: SessionState):
        self.session = session
        self.priority_checker = PriorityChecker(session)

    def select_next_question(self) -> QuestionSelection:
        priority, reason = self.priority_checker.check_priority()
        q_type = self._select_question_type(priority)
        target = self._identify_target(priority, q_type)
        generator = QuestionGenerator(self.session, target)
        question_text = generator.generate_question(q_type)
        context = _PRIORITY_CONTEXT.get(priority)
        return QuestionSelection(
            question_text=question_text,
            question_type=q_type,
            priority=priority,
            priority_reason=reason,
            context=context,
        )

    def _select_question_type(self, priority: Priority) -> QuestionType:
        if priority == Priority.CRITICAL:
            return QuestionType.CONTROL_CHECK
        if priority == Priority.HIGH:
            return QuestionType.ALTERNATIVES_CHECK
        if priority == Priority.MEDIUM:
            active = self.session.get_active_hypotheses()
            if len(active) >= 5:
                structural = [h for h in active if h.type == HypothesisType.STRUCTURAL]
                if not structural:
                    return QuestionType.LEVEL_CHECK
            return QuestionType.FUNCTION_CHECK
        if priority == Priority.LOW:
            return QuestionType.DYNAMICS_CHECK
        # NONE — turn-based fallback
        turns = self.session.progress.dialogue_turns
        if turns < 3:
            return QuestionType.FUNCTION_CHECK
        if turns < 7:
            return QuestionType.LEVEL_CHECK if turns % 2 == 0 else QuestionType.FUNCTION_CHECK
        return QuestionType.DYNAMICS_CHECK

    def _identify_target(
        self, priority: Priority, q_type: QuestionType
    ) -> Optional[Hypothesis]:
        active = self.session.get_active_hypotheses()
        if not active:
            return None
        if q_type == QuestionType.ALTERNATIVES_CHECK:
            dominant = [h for h in active if h.confidence == ConfidenceLevel.DOMINANT]
            if dominant:
                return dominant[0]
        if q_type == QuestionType.FUNCTION_CHECK:
            structural = [h for h in active if h.type == HypothesisType.STRUCTURAL]
            if structural:
                no_fn = [h for h in structural if not h.function]
                return no_fn[0] if no_fn else structural[0]
        return active[-1]

    def should_continue_dialogue(self) -> Tuple[bool, str]:
        max_turns = 20
        if self.session.progress.dialogue_turns >= max_turns:
            return False, f"Достигнут лимит ({max_turns} вопросов)"
        if self.session.can_proceed_to_output():
            return False, "Минимальная модель достигнута — готово к концептуализации"
        if self.session.has_blocking_flags():
            blocking = self.session.get_blocking_red_flags()
            return False, f"Блокировано флагом: {blocking[0].description}"
        return True, "Модель неполная — продолжаем диалог"


_PRIORITY_CONTEXT = {
    Priority.CRITICAL: "Критический вопрос для определения точки управления.",
    Priority.HIGH: "Тестируем гипотезу против альтернатив.",
    Priority.MEDIUM: "Организуем гипотезы по архитектуре.",
    Priority.LOW: "Уточняем понимание динамики.",
}


# ── Convenience functions ─────────────────────────────────────────────────────

def select_next_question(session: SessionState) -> QuestionSelection:
    return DecisionPolicySelector(session).select_next_question()


def should_continue_dialogue(session: SessionState) -> Tuple[bool, str]:
    return DecisionPolicySelector(session).should_continue_dialogue()
