"""Async hypothesis extraction using Claude API."""
import json
import logging

from anthropic import AsyncAnthropic

from app.config import settings

from .enums import ConfidenceLevel, HypothesisType, PsycheLevelEnum
from .models import Hypothesis, SessionState

logger = logging.getLogger(__name__)

_ANTHROPIC_MODEL = "claude-sonnet-4-5-20250929"

_EXTRACT_HYPOTHESIS_PROMPT = """\
Ты - эксперт по анализу психотерапевтических концептуализаций в рамках PsycheOS framework.

Твоя задача: извлечь структурированную гипотезу из ответа специалиста.

# PsycheOS Framework (кратко)

## Слои (L0-L4):
- L0: Базовая регуляция (энергия, сон, тело, витальность)
- L1: Рефлексивный контроль (автоматизмы, привычки, защиты)
- L2: Сознательный выбор (произвольная регуляция, решения)
- L3: Социально-ролевой контроль (отношения, роли, социальная идентичность)
- L4: Смыслы и идентичность (ценности, нарратив, "кто я")

## Типы гипотез (КРИТИЧЕСКИ ВАЖНО РАЗЛИЧАТЬ):

### MANAGERIAL (🎯 ПРИОРИТЕТ)
**Признаки:**
- Описывает ГДЕ и КАК можно влиять
- Использует: "можно", "нужно", "стоит", "начать с", "вмешаться", "воздействовать"
- Указывает на ТОЧКУ УПРАВЛЕНИЯ, leverage point
- Говорит что "может измениться", "можно изменить"
- Предлагает последовательность действий

**Примеры:**
- "Стоит начать с L0 - стабилизировать сон"
- "Можно вмешаться в зазор между L3 и L0"
- "Критическая точка - переход L3 → L0, здесь можно влиять"

### DYNAMIC
**Признаки:**
- Описывает МЕХАНИЗМЫ поддержания во времени
- Использует: "поддерживается", "петля", "цикл", "подкрепляется"
- Объясняет ПОЧЕМУ паттерн сохраняется
- Стрелки и последовательности: A → B → C

**Примеры:**
- "L3 требует → L0 мобилизуется → истощение → избегание → закрепление"
- "Петля: тревога → избегание → краткосрочное облегчение → усиление"

### FUNCTIONAL
**Признаки:**
- Описывает ЧТО система получает от паттерна
- Использует: "защищает", "предотвращает", "служит для", "функция"
- Объясняет ЗАЧЕМ система это делает

**Примеры:**
- "Паттерн защищает от осознания утраты"
- "Избегание предотвращает аффективный коллапс"

### STRUCTURAL
**Признаки:**
- Описывает КОНФИГУРАЦИЮ системы
- Констатирует КАК устроено (без объяснения зачем или как поддерживается)
- Описательная, не объясняющая

**Примеры:**
- "Система в режиме выживания с ригидной защитой"
- "Конфигурация: L0 истощён, L1 гиперактивен"

# АЛГОРИТМ КЛАССИФИКАЦИИ

1. Если текст содержит "можно", "нужно", "стоит", "вмешаться", "влиять", "начать с", "изменить" → **ПРОВЕРИТЬ: это MANAGERIAL?**
2. Если описывает петли, стрелки A→B→C, механизмы → DYNAMIC
3. Если объясняет зачем, функцию, что защищает → FUNCTIONAL
4. Только если просто описывает конфигурацию → STRUCTURAL

# ЯЗЫК ФОРМУЛИРОВОК (поле "formulation"):
# - Пиши на профессиональном разговорном языке, понятном без знания фреймворка
# - НЕ используй в тексте: "L0", "L1", "L2", "L3", "L4", "слой", "система", "маркер", "регуляция", "паттерн системы"
# - Говори о клиенте конкретно: "клиент физически истощён", "избегает ситуаций без ясного ответа", "понимает что нужно изменить, но не может"
# - Поля "levels" в JSON заполняй правильно обозначениями (L0-L4) — но в текст формулировки их не включай
# - Одно-два предложения максимум

# Формат ответа (строго JSON):

{
  "type": "managerial|dynamic|functional|structural",
  "levels": ["L0", "L1", ...],
  "formulation": "формулировка на разговорном языке, без L0-L4 и терминов фреймворка",
  "confidence": "weak|working|dominant",
  "reasoning": "почему определён именно этот тип"
}

КРИТИЧЕСКИ ВАЖНО: если в тексте есть указание НА ЧТО МОЖНО ВЛИЯТЬ или ЧТО ИЗМЕНИТЬ - это MANAGERIAL!

ОТВЕТ ДОЛЖЕН БЫТЬ ТОЛЬКО JSON, БЕЗ ДОПОЛНИТЕЛЬНОГО ТЕКСТА.\
"""


def _post_process_type(formulation: str, extracted_type: str) -> str:
    """Override to managerial if 2+ managerial markers found in formulation."""
    markers = [
        "можно", "нужно", "стоит", "начать с", "вмешаться",
        "воздействовать", "влиять", "изменить", "скорректировать",
        "работать с", "фокус на", "приоритет", "критическая точка",
        "точка управления", "leverage",
    ]
    fl = formulation.lower()
    count = sum(1 for m in markers if m in fl)
    if count >= 2 and extracted_type != "managerial":
        logger.warning(
            f"[conceptualizator] Type override: '{extracted_type}' → 'managerial' "
            f"({count} managerial markers)"
        )
        return "managerial"
    return extracted_type


def _parse_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```json"):
        t = t[7:]
    if t.startswith("```"):
        t = t[3:]
    if t.endswith("```"):
        t = t[:-3]
    return json.loads(t.strip())


async def extract_hypothesis_from_response(
    message: str, session: SessionState
) -> Hypothesis:
    """Extract a structured hypothesis from the specialist's message using Claude."""
    prior_ctx_parts = []
    if session.screen_context:
        prior_ctx_parts.append(f"### Данные скрининга:\n{session.screen_context[:500]}")
    if session.interpreter_context:
        prior_ctx_parts.append(f"### Данные интерпретации:\n{session.interpreter_context[:500]}")
    prior_ctx = (
        "\n\nФоновые данные кейса:\n" + "\n\n".join(prior_ctx_parts) + "\n\n"
        if prior_ctx_parts else ""
    )

    user_message = (
        f"Контекст сессии:\n"
        f"- Текущих гипотез: {len(session.get_active_hypotheses())}\n"
        f"- Управленческих гипотез: {len(session.get_managerial_hypotheses())}\n"
        f"- Вопросов задано: {session.progress.dialogue_turns}\n"
        f"{prior_ctx}"
        f"Ответ специалиста:\n{message}\n\n"
        "Извлеки структурированную гипотезу из этого ответа.\n"
        'ВАЖНО: Если текст содержит слова "можно", "нужно", "стоит", '
        '"вмешаться" - это MANAGERIAL!'
    )
    try:
        client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        resp = await client.messages.create(
            model=_ANTHROPIC_MODEL,
            max_tokens=1000,
            system=_EXTRACT_HYPOTHESIS_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        data = _parse_json(resp.content[0].text)
        logger.debug(f"[conceptualizator] Claude hypothesis data: {data}")

        corrected_type = _post_process_type(data["formulation"], data["type"])
        data["type"] = corrected_type

        hyp_id = f"hyp_{session.progress.hypotheses_added + 1:03d}"
        return Hypothesis(
            id=hyp_id,
            type=HypothesisType(data["type"]),
            levels=[PsycheLevelEnum(lv) for lv in data["levels"]],
            formulation=data["formulation"],
            confidence=ConfidenceLevel(data["confidence"]),
            foundations=[data.get("reasoning", "")],
        )
    except Exception:
        logger.exception("[conceptualizator] Error extracting hypothesis; using fallback")
        return Hypothesis(
            id=f"hyp_{session.progress.hypotheses_added + 1:03d}",
            type=HypothesisType.STRUCTURAL,
            levels=[PsycheLevelEnum.L0],
            formulation=message[:300],
            confidence=ConfidenceLevel.WEAK,
            foundations=["Fallback extraction"],
        )
