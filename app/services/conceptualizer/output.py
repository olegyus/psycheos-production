"""Async output assembly (Layers A, B, C) using Claude API."""
import json
import logging

from anthropic import AsyncAnthropic

from app.config import settings

from .enums import PsycheLevelEnum
from .models import (
    ConceptualizationOutput,
    InterventionTarget,
    LayerA,
    LayerB,
    LayerC,
    SessionState,
)

logger = logging.getLogger(__name__)

_ANTHROPIC_MODEL = "claude-sonnet-4-5-20250929"

# ── Prompts ───────────────────────────────────────────────────────────────────

_LAYER_A_PROMPT = """\
Ты - эксперт по системному анализу психотерапевтических случаев в рамках PsycheOS framework.

Твоя задача: создать Layer A - техническую концептуальную модель системы клиента для специалиста.

# Входные данные

Тебе предоставлены:
1. Все гипотезы сессии (structural, functional, dynamic, managerial)
2. Данные по слоям L0-L4

# PsycheOS Framework

Слои:
- L0: Базовая регуляция (энергия, сон, витальность)
- L1: Рефлексивный контроль (автоматизмы, защиты)
- L2: Сознательный выбор (произвольная регуляция)
- L3: Социально-ролевой контроль (отношения, роли)
- L4: Смыслы и идентичность (ценности, нарратив)

# Твоя задача

Создай Layer A со следующими компонентами:

## 1. Ведущая гипотеза (leading_formulation)
Выбери STRUCTURAL гипотезу с максимальной уверенностью.
Если нет structural - выбери любую с highest confidence.

## 2. Доминирующий слой (dominant_layer)
КРИТИЧЕСКИ ВАЖНО: определи НЕ по количеству упоминаний, а по УПРАВЛЯЮЩЕМУ КОНФЛИКТУ.
- Где находится источник напряжения?
- Какой слой УПРАВЛЯЕТ конфигурацией?
- L0 может быть НОСИТЕЛЕМ напряжения, но не источником конфликта!

Примеры:
- Если L3 (идентичность) → L0 (мобилизация) → истощение, то dominant = L3
- Если L4 (смысл) требует перфоманса → L0 истощение, то dominant = L4

## 3. Конфигурация (configuration_summary)
НЕ описывай абзацем! Покажи ПЕТЛИ ОБРАТНОЙ СВЯЗИ стрелками:

Формат: "Триггер → Слой X (реакция) → Слой Y (последствие) → Подкрепление"

Пример:
"Оценка результата (L3) → Интерпретация как угроза идентичности (L4) →
Мобилизация L0 на пределе → Истощение → Избегание L1 →
Краткосрочное снижение тревоги → Закрепление стратегии L2"

## 4. Цена системы (system_cost)
Для каждого слоя укажи КОНКРЕТНУЮ цену:
- L0: энергетическая (что истощается)
- L3: социальная (какие связи страдают)
- L4: семантическая (какие возможности закрыты)

# Формат ответа (JSON):

{
  "leading_formulation": "формулировка ведущей гипотезы",
  "dominant_layer": "L0|L1|L2|L3|L4",
  "dominant_layer_reasoning": "почему именно этот слой управляет",
  "configuration_summary": "петли со стрелками A→B→C→reinforcement",
  "system_cost": {
    "energetic": "конкретная цена L0",
    "social": "конкретная цена L3",
    "semantic": "конкретная цена L4"
  }
}

ОТВЕТ ТОЛЬКО JSON, БЕЗ ДОПОЛНИТЕЛЬНОГО ТЕКСТА.\
"""

_LAYER_B_PROMPT = """\
Ты - эксперт по терапевтическим вмешательствам в рамках PsycheOS framework.

Твоя задача: создать Layer B - мишени вмешательства для специалиста.

# КРИТИЧЕСКИ ВАЖНО

Layer B - это НЕ описание паттерна, а ФОРМУЛИРОВКИ ИЗМЕНЕНИЯ!

❌ НЕПРАВИЛЬНО: "Система в режиме выживания"
✅ ПРАВИЛЬНО: "Нормализация циркадных ритмов, снижение симпатической активации"

# Твоя задача

Создай 3-5 мишеней вмешательства на основе MANAGERIAL гипотез.

Каждая мишень должна содержать:

## 1. Layer (слой)
На каком слое или интерфейсе работать.

## 2. Direction (направление изменения)
ЧТО должно измениться, НЕ КАК это делать!

Примеры правильных формулировок:
- L0: "Снижение вегетативной реактивности, восстановление энергетического баланса"
- L1: "Экспозиция к завершению с контролируемым аффектом"
- L2: "Пересборка стратегии 'ошибка = катастрофа'"
- L3: "Деконструкция слияния 'я = мой результат'"
- L4: "Работа с условной моделью принадлежности"

## 3. Priority (1-5)
1 = критический, 5 = вспомогательный

Правило: L0 стабилизация = приоритет 1-2, L4 работа = приоритет 4-5

## 4. Rationale (обоснование)
Почему эта мишень важна и что она даст.

# Формат ответа (JSON):

{
  "targets": [
    {
      "layer": "L0|L1|L2|L3|L4|interface_LX_LY",
      "direction": "формулировка изменения (не описание!)",
      "priority": 1-5,
      "rationale": "обоснование важности"
    }
  ],
  "sequencing_notes": "рекомендация по последовательности"
}

ВАЖНО: Направления должны быть КОНКРЕТНЫМИ и ACTIONABLE (пусть и без указания метода).

ОТВЕТ ТОЛЬКО JSON, БЕЗ ДОПОЛНИТЕЛЬНОГО ТЕКСТА.\
"""

_LAYER_C_PROMPT = """\
Ты - эксперт по созданию метафорических нарративов для клиентов.

Твоя задача: создать Layer C - метафорический нарратив, который клиент УЗНАЕТ.

# КРИТИЧЕСКИ ВАЖНО

- Метафора должна быть СПЕЦИФИЧНА для конфликта, не клише!
- Нарратив - на языке опыта, БЕЗ слоёв/гипотез/диагнозов
- Клиент должен сказать "Да, это про меня"

# Твоя задача

## 1. Метафора (core_metaphor)
Создай ONE образ (3-6 слов), который схватывает УПРАВЛЯЮЩИЙ КОНФЛИКТ.

❌ Избегай клише:
- "Двигатель на пределе" (слишком общо)
- "Замкнутый круг" (не образно)

✅ Хорошие примеры:
- "Экзамен, который никогда не начинается" (избегание оценки)
- "Охранник, который не может уйти с поста" (ригидная бдительность)
- "Дом, где нельзя открыть окна" (удушающий контроль)

## 2. Нарратив (200-300 слов)
Опиши ОПЫТ клиента через метафору.

Структура:
- Открытие: "Вы как..." (узнаваемая ситуация)
- Середина: динамика системы (метафорически)
- Закрытие: что становится возможным

Язык:
- Второе лицо ("Вы...")
- Сенсорный (ощущения, образы)
- Без техжаргона (L0-L4, гипотезы, диагнозы)

## 3. Направление изменения (direction_of_change)
Что может стать возможным (1-2 предложения).

НЕ директивно! "Можно попробовать...", "Становится возможным..."

# Формат ответа (JSON):

{
  "core_metaphor": "образ (3-6 слов)",
  "narrative": "текст 200-300 слов на языке опыта",
  "direction_of_change": "что становится возможным"
}

ОТВЕТ ТОЛЬКО JSON, БЕЗ ДОПОЛНИТЕЛЬНОГО ТЕКСТА.\
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```json"):
        t = t[7:]
    if t.startswith("```"):
        t = t[3:]
    if t.endswith("```"):
        t = t[:-3]
    return json.loads(t.strip())


def _hypotheses_context(session: SessionState) -> str:
    lines = ["# Гипотезы:\n"]
    for hyp in session.get_active_hypotheses():
        levels_str = ", ".join(l.value for l in hyp.levels)
        lines.append(f"**{hyp.type.value.upper()}** [{levels_str}]")
        lines.append(hyp.formulation)
        lines.append(f"Уверенность: {hyp.confidence.value}\n")
    return "\n".join(lines)


# ── Layer assemblers ──────────────────────────────────────────────────────────

async def _assemble_layer_a(session: SessionState) -> LayerA:
    hyp_ctx = _hypotheses_context(session)
    user_message = (
        f"{hyp_ctx}\n\n"
        "На основе этих гипотез создай Layer A - техническую модель для специалиста.\n\n"
        "КРИТИЧЕСКИ ВАЖНО:\n"
        "1. Dominant layer - определи по УПРАВЛЯЮЩЕМУ КОНФЛИКТУ, не по частоте упоминаний\n"
        "2. Configuration - покажи петли со СТРЕЛКАМИ (A→B→C), не абзацем\n"
        "3. System cost - конкретная цена для L0, L3, L4"
    )
    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    resp = await client.messages.create(
        model=_ANTHROPIC_MODEL,
        max_tokens=2000,
        system=_LAYER_A_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    data = _parse_json(resp.content[0].text)

    hypotheses = session.get_active_hypotheses()
    supporting_points = [
        f"{h.type.value}: {h.formulation[:100]}"
        for h in hypotheses[:4]
        if h.formulation != data["leading_formulation"]
    ]

    cost = data["system_cost"]
    system_cost = (
        f"Энергетическая цена (L0): {cost['energetic']}\n"
        f"Социальная цена (L3): {cost['social']}\n"
        f"Семантическая цена (L4): {cost['semantic']}"
    )

    layer_a = LayerA(
        leading_formulation=data["leading_formulation"],
        supporting_points=supporting_points,
        dominant_layer=PsycheLevelEnum(data["dominant_layer"]),
        configuration_summary=data["configuration_summary"],
        system_cost=system_cost,
    )
    logger.info(f"[conceptualizator] Layer A done. Dominant: {layer_a.dominant_layer.value}")
    return layer_a


async def _assemble_layer_b(session: SessionState) -> LayerB:
    managerial = session.get_managerial_hypotheses()
    if not managerial:
        raise ValueError("No managerial hypotheses for Layer B")

    context_lines = ["# Управленческие гипотезы:\n"]
    for hyp in managerial:
        levels_str = ", ".join(l.value for l in hyp.levels)
        context_lines.append(f"[{levels_str}] {hyp.formulation}\n")

    user_message = (
        "\n".join(context_lines) + "\n\n"
        "На основе этих управленческих гипотез создай Layer B - мишени вмешательства.\n\n"
        "КРИТИЧЕСКИ ВАЖНО:\n"
        "- Direction = ЧТО должно измениться, НЕ описание паттерна!\n"
        "- Формулировки конкретные и actionable\n"
        "- Приоритеты: L0 = 1-2, L4 = 4-5"
    )
    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    resp = await client.messages.create(
        model=_ANTHROPIC_MODEL,
        max_tokens=2000,
        system=_LAYER_B_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    data = _parse_json(resp.content[0].text)

    targets = [
        InterventionTarget(
            layer=t["layer"],
            direction=t["direction"],
            priority=t["priority"],
            rationale=t["rationale"],
        )
        for t in data["targets"]
    ]
    layer_b = LayerB(targets=targets, sequencing_notes=data["sequencing_notes"])
    logger.info(f"[conceptualizator] Layer B done. Targets: {len(targets)}")
    return layer_b


async def _assemble_layer_c(session: SessionState) -> LayerC:
    context_lines = ["# Гипотезы для понимания конфликта:\n"]
    for hyp in session.get_active_hypotheses():
        context_lines.append(f"{hyp.type.value}: {hyp.formulation}\n")

    user_message = (
        "\n".join(context_lines) + "\n\n"
        "На основе этого понимания создай Layer C - метафорический нарратив для клиента.\n\n"
        "КРИТИЧЕСКИ ВАЖНО:\n"
        "- Метафора должна схватывать УПРАВЛЯЮЩИЙ КОНФЛИКТ, не симптом\n"
        "- Нарратив на языке ОПЫТА, без L0-L4, гипотез, диагнозов\n"
        "- Клиент должен узнать себя"
    )
    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    resp = await client.messages.create(
        model=_ANTHROPIC_MODEL,
        max_tokens=2000,
        system=_LAYER_C_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    data = _parse_json(resp.content[0].text)

    layer_c = LayerC(
        core_metaphor=data["core_metaphor"],
        narrative=data["narrative"],
        direction_of_change=data["direction_of_change"],
    )
    logger.info(f"[conceptualizator] Layer C done. Metaphor: {layer_c.core_metaphor}")
    return layer_c


# ── Public entry point ────────────────────────────────────────────────────────

async def assemble_output(session: SessionState) -> ConceptualizationOutput:
    """Assemble complete three-layer conceptualization via Claude."""
    if not session.can_proceed_to_output():
        raise ValueError("Session not ready for output assembly")
    layer_a = await _assemble_layer_a(session)
    layer_b = await _assemble_layer_b(session)
    layer_c = await _assemble_layer_c(session)
    return ConceptualizationOutput(
        session_id=session.session_id,
        layer_a=layer_a,
        layer_b=layer_b,
        layer_c=layer_c,
    )
