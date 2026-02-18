"""
PsycheOS Interpreter — Structured Results
JSON schema validation and TXT report formatting.

Key difference from standalone bot:
  format_to_txt() returns the content as a str (not written to disk).
  Callers send it via Telegram as an in-memory document.
"""
from typing import Any, Dict, Tuple


def validate_structured_results(data: Dict[str, Any]) -> Tuple[bool, list[str]]:
    """
    Validate Structured Results JSON against schema.

    Returns:
        (is_valid, list_of_errors)
    """
    errors: list[str] = []

    required_fields = [
        "meta", "input_summary", "phenomenological_summary",
        "interpretative_hypotheses", "focus_of_tension",
        "compensatory_patterns", "uncertainty_profile",
        "clarification_directions", "policy_flags",
    ]
    for field in required_fields:
        if field not in data:
            errors.append(f"Missing required field: {field}")

    if errors:
        return False, errors

    for field in ["session_id", "timestamp", "state", "mode", "iteration_count"]:
        if field not in data.get("meta", {}):
            errors.append(f"Missing meta.{field}")

    hypotheses = data.get("interpretative_hypotheses", [])
    mode = data.get("meta", {}).get("mode", "STANDARD")
    if mode == "LOW_DATA" and len(hypotheses) > 1:
        errors.append(f"LOW_DATA mode allows max 1 hypothesis, got {len(hypotheses)}")
    elif mode == "STANDARD" and len(hypotheses) > 3:
        errors.append(f"STANDARD mode allows max 3 hypotheses, got {len(hypotheses)}")

    profile = data.get("uncertainty_profile", {})
    if not profile.get("data_gaps") and not profile.get("ambiguities"):
        errors.append("Uncertainty profile lacks substantive content")

    return len(errors) == 0, errors


def format_to_txt(data: Dict[str, Any]) -> str:
    """
    Format Structured Results JSON into a human-readable TXT report.

    Returns:
        Report content as a string (UTF-8). Caller is responsible for
        encoding and sending as a Telegram document.
    """
    lines: list[str] = []

    # ── Header ────────────────────────────────────────────────────────────────
    lines += [
        "=" * 80,
        "PsycheOS INTERPRETER — РЕЗУЛЬТАТЫ ИНТЕРПРЕТАЦИИ",
        "=" * 80,
        "",
    ]

    meta = data.get("meta", {})
    lines += [
        f"Сессия: {meta.get('session_id', 'Н/Д')}",
        f"Дата: {meta.get('timestamp', 'N/A')}",
        f"Режим: {meta.get('mode', 'N/A')}",
        "",
        "-" * 80,
        "",
    ]

    # ── Input summary ─────────────────────────────────────────────────────────
    input_sum = data.get("input_summary", {})
    material_types = {
        "dream": "Сон",
        "drawing": "Рисунок",
        "image_series": "Серия образов",
        "mixed": "Смешанный",
    }
    sources = {
        "client_report": "Рассказ клиента",
        "specialist_observation": "Наблюдение специалиста",
        "therapeutic_session": "Терапевтическая сессия",
    }
    completeness_map = {
        "sufficient": "Достаточно",
        "partial": "Частично",
        "fragmentary": "Фрагментарно",
    }

    lines += [
        "ИСХОДНЫЙ МАТЕРИАЛ",
        "",
        f"Тип материала: {material_types.get(input_sum.get('material_type', ''), 'Не указано')}",
        f"Источник: {sources.get(input_sum.get('source', ''), 'Не указан')}",
        f"Полнота данных: {completeness_map.get(input_sum.get('completeness', ''), 'Не указана')}",
    ]

    clarifications = input_sum.get("clarifications_received", [])
    if clarifications:
        lines += ["", "Уточнения:"]
        for i, clar in enumerate(clarifications, 1):
            lines.append(f"  {i}. {clar}")

    lines += ["", "-" * 80, ""]

    # ── Phenomenological summary ──────────────────────────────────────────────
    phenom = data.get("phenomenological_summary", {})
    lines += ["ФЕНОМЕНОЛОГИЧЕСКОЕ ОПИСАНИЕ", "", phenom.get("text", "N/A")]

    key_elements = phenom.get("key_elements", [])
    if key_elements:
        lines += ["", "Ключевые элементы:"]
        for elem in key_elements:
            lines.append(f"  • [{elem.get('prominence', 'N/A').upper()}] {elem.get('element', 'N/A')}")
            if elem.get("description"):
                lines.append(f"    {elem['description']}")

    lines += ["", "-" * 80, ""]

    # ── Interpretative hypotheses ─────────────────────────────────────────────
    hypotheses = data.get("interpretative_hypotheses", [])
    lines += ["ИНТЕРПРЕТАТИВНЫЕ ГИПОТЕЗЫ", ""]

    if not hypotheses:
        lines.append("(Недостаточно данных для формулировки гипотез)")
    else:
        for i, hyp in enumerate(hypotheses, 1):
            lines += [f"ГИПОТЕЗА {i}", "", hyp.get("hypothesis_text", "N/A"), ""]

            evidence = hyp.get("supporting_evidence", [])
            if evidence:
                lines.append("Поддерживающие элементы:")
                for ev in evidence:
                    lines.append(f"  • {ev}")
                lines.append("")

            if hyp.get("limitations"):
                lines += ["Ограничения:", f"  {hyp['limitations']}", ""]

            alternatives = hyp.get("alternatives", [])
            if alternatives:
                lines.append("Альтернативные интерпретации:")
                for alt in alternatives:
                    lines.append(f"  • {alt}")
                lines.append("")

    lines += ["-" * 80, ""]

    # ── Focus of tension ──────────────────────────────────────────────────────
    focus = data.get("focus_of_tension", {})
    lines += ["ОБЛАСТИ НАПРЯЖЕНИЯ", ""]

    domain_names = {
        "safety_and_protection": "Безопасность и защита",
        "connection_and_belonging": "Связь и принадлежность",
        "autonomy_and_control": "Автономия и контроль",
        "change_and_uncertainty": "Изменения и неопределённость",
        "identity_and_continuity": "Идентичность и непрерывность",
        "meaning_and_purpose": "Смысл и цель",
        "resource_management": "Управление ресурсами",
    }
    domains = focus.get("domains", [])
    if domains:
        lines.append("Домены:")
        for d in domains:
            lines.append(f"  • {domain_names.get(d, d)}")

    indicators = focus.get("indicators", [])
    if indicators:
        lines += ["", "Индикаторы:"]
        for ind in indicators:
            lines.append(f"  • {ind}")

    lines += ["", "-" * 80, ""]

    # ── Compensatory patterns ─────────────────────────────────────────────────
    patterns = data.get("compensatory_patterns", [])
    if patterns:
        pattern_names = {
            "distancing": "Дистанцирование",
            "control_seeking": "Поиск контроля",
            "symbolic_repair": "Символическое восстановление",
            "affect_modulation": "Модуляция аффекта",
            "fragmentation": "Фрагментация",
            "idealization": "Идеализация",
            "externalization": "Экстернализация",
            "other": "Другое",
        }
        lines += ["КОМПЕНСАТОРНЫЕ ПАТТЕРНЫ", ""]
        for patt in patterns:
            lines.append(
                f"• {pattern_names.get(patt.get('pattern', ''), patt.get('pattern', 'N/A'))} "
                f"({patt.get('confidence', 'N/A')})"
            )
            if patt.get("evidence"):
                lines.append(f"  {patt['evidence']}")
            lines.append("")
        lines += ["-" * 80, ""]

    # ── Uncertainty profile ───────────────────────────────────────────────────
    uncertainty = data.get("uncertainty_profile", {})
    lines += [
        "ПРОФИЛЬ НЕОПРЕДЕЛЁННОСТИ",
        "",
        f"Общая уверенность: {uncertainty.get('overall_confidence', 'N/A').upper()}",
        "",
    ]

    for label, key in [
        ("Недостающие данные:", "data_gaps"),
        ("Неоднозначности:", "ambiguities"),
        ("Предостережения:", "cautions"),
    ]:
        items = uncertainty.get(key, [])
        if items:
            lines.append(label)
            for item in items:
                lines.append(f"  • {item}")
            lines.append("")

    lines += ["-" * 80, ""]

    # ── Clarification directions ──────────────────────────────────────────────
    directions = data.get("clarification_directions", [])
    if directions:
        lines += ["НАПРАВЛЕНИЯ ДЛЯ УТОЧНЕНИЯ", ""]
        for direction in directions:
            lines.append(
                f"[{direction.get('priority', 'medium').upper()}] {direction.get('direction', '')}"
            )
            if direction.get("rationale"):
                lines.append(f"  Обоснование: {direction['rationale']}")
            lines.append("")

    # ── Footer ────────────────────────────────────────────────────────────────
    lines += ["=" * 80, "Конец отчёта", "=" * 80]

    return "\n".join(lines)
