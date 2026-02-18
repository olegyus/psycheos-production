"""Цели сессии — labels для UI и описания."""

from data.schemas import SessionGoal

GOAL_LABELS: dict[SessionGoal, str] = {
    SessionGoal.CONTACT_STABILIZATION: "Установление контакта и стабилизация",
    SessionGoal.DIAGNOSTIC_CLARIFICATION: "Диагностическое уточнение",
    SessionGoal.SYMPTOM_WORK: "Работа с симптомом",
    SessionGoal.REGULATORY_CONFLICT: "Работа с регуляторным конфликтом",
    SessionGoal.COGNITIVE_RESTRUCTURING: "Когнитивная реструктуризация",
    SessionGoal.AFFECT_WORK: "Работа с аффектом",
    SessionGoal.CRISIS_SUPPORT: "Поддержка в кризисе",
    SessionGoal.THERAPY_TERMINATION: "Завершение терапии",
}

MODE_LABELS = {
    "TRAINING": "🎓 Обучение (сигнал + объяснение)",
    "PRACTICE": "🏋️ Тренировка (только сигнал)",
}
