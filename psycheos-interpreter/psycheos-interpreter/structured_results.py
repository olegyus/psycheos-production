"""
PsycheOS Interpreter Bot - Structured Results
JSON validation and TXT formatting
"""
import json
from datetime import datetime
from typing import Dict, Any
from pathlib import Path


def validate_structured_results(data: Dict[str, Any]) -> tuple[bool, list[str]]:
    """
    Validate Structured Results JSON against schema.
    
    Returns:
        (is_valid, list_of_errors)
    """
    errors = []
    
    # Required root fields
    required_fields = [
        'meta', 'input_summary', 'phenomenological_summary',
        'interpretative_hypotheses', 'focus_of_tension',
        'compensatory_patterns', 'uncertainty_profile',
        'clarification_directions', 'policy_flags'
    ]
    
    for field in required_fields:
        if field not in data:
            errors.append(f"Missing required field: {field}")
    
    if errors:
        return False, errors
    
    # Validate meta
    if 'meta' in data:
        for field in ['session_id', 'timestamp', 'state', 'mode', 'iteration_count']:
            if field not in data['meta']:
                errors.append(f"Missing meta.{field}")
    
    # Validate hypothesis count
    if 'interpretative_hypotheses' in data:
        count = len(data['interpretative_hypotheses'])
        mode = data.get('meta', {}).get('mode', 'STANDARD')
        
        if mode == 'LOW_DATA' and count > 1:
            errors.append(f"LOW_DATA mode allows max 1 hypothesis, got {count}")
        elif mode == 'STANDARD' and count > 3:
            errors.append(f"STANDARD mode allows max 3 hypotheses, got {count}")
    
    # Validate uncertainty presence
    if 'uncertainty_profile' in data:
        profile = data['uncertainty_profile']
        if not profile.get('data_gaps') and not profile.get('ambiguities'):
            errors.append("Uncertainty profile lacks substantive content")
    
    return len(errors) == 0, errors


def format_to_txt(data: Dict[str, Any], output_path: Path) -> str:
    """
    Format Structured Results JSON into beautiful TXT file.
    
    Args:
        data: Structured Results JSON
        output_path: Path to save TXT file
    
    Returns:
        Path to created file
    """
    lines = []
    
    # Header
    lines.append("=" * 80)
    lines.append("PsycheOS INTERPRETER — РЕЗУЛЬТАТЫ ИНТЕРПРЕТАЦИИ")
    lines.append("=" * 80)
    lines.append("")
    
    # Meta info
    meta = data.get('meta', {})
    lines.append(f"Сессия: {meta.get('session_id', 'Н/Д')}")
    lines.append(f"Дата: {meta.get('timestamp', 'N/A')}")
    lines.append(f"Режим: {meta.get('mode', 'N/A')}")
    lines.append("")
    lines.append("-" * 80)
    lines.append("")
    
    # Input summary
    input_sum = data.get('input_summary', {})
    lines.append("ИСХОДНЫЙ МАТЕРИАЛ")
    lines.append("")
    # Переводим типы материала
    material_types = {
        'dream': 'Сон',
        'drawing': 'Рисунок',
        'image_series': 'Серия образов',
        'mixed': 'Смешанный'
    }
    material_type = material_types.get(input_sum.get('material_type', ''), 'Не указано')

    # Переводим источники
    sources = {
        'client_report': 'Рассказ клиента',
        'specialist_observation': 'Наблюдение специалиста',
        'therapeutic_session': 'Терапевтическая сессия'
    }
    source = sources.get(input_sum.get('source', ''), 'Не указан')

    # Переводим полноту
    completeness_map = {
        'sufficient': 'Достаточно',
        'partial': 'Частично',
        'fragmentary': 'Фрагментарно'
    }
    completeness = completeness_map.get(input_sum.get('completeness', ''), 'Не указана')

    lines.append(f"Тип материала: {material_type}")
    lines.append(f"Источник: {source}")
    lines.append(f"Полнота данных: {completeness}")
    
    clarifications = input_sum.get('clarifications_received', [])
    if clarifications:
        lines.append("")
        lines.append("Уточнения:")
        for i, clar in enumerate(clarifications, 1):
            lines.append(f"  {i}. {clar}")
    
    lines.append("")
    lines.append("-" * 80)
    lines.append("")
    
    # Phenomenological summary
    phenom = data.get('phenomenological_summary', {})
    lines.append("ФЕНОМЕНОЛОГИЧЕСКОЕ ОПИСАНИЕ")
    lines.append("")
    lines.append(phenom.get('text', 'N/A'))
    
    key_elements = phenom.get('key_elements', [])
    if key_elements:
        lines.append("")
        lines.append("Ключевые элементы:")
        for elem in key_elements:
            prom = elem.get('prominence', 'N/A')
            name = elem.get('element', 'N/A')
            desc = elem.get('description', '')
            lines.append(f"  • [{prom.upper()}] {name}")
            if desc:
                lines.append(f"    {desc}")
    
    lines.append("")
    lines.append("-" * 80)
    lines.append("")
    
    # Interpretative hypotheses
    hypotheses = data.get('interpretative_hypotheses', [])
    lines.append("ИНТЕРПРЕТАТИВНЫЕ ГИПОТЕЗЫ")
    lines.append("")
    
    if not hypotheses:
        lines.append("(Недостаточно данных для формулировки гипотез)")
    else:
        for i, hyp in enumerate(hypotheses, 1):
            lines.append(f"ГИПОТЕЗА {i}")
            lines.append("")
            lines.append(hyp.get('hypothesis_text', 'N/A'))
            lines.append("")
            
            evidence = hyp.get('supporting_evidence', [])
            if evidence:
                lines.append("Поддерживающие элементы:")
                for ev in evidence:
                    lines.append(f"  • {ev}")
                lines.append("")
            
            limitations = hyp.get('limitations', '')
            if limitations:
                lines.append("Ограничения:")
                lines.append(f"  {limitations}")
                lines.append("")
            
            alternatives = hyp.get('alternatives', [])
            if alternatives:
                lines.append("Альтернативные интерпретации:")
                for alt in alternatives:
                    lines.append(f"  • {alt}")
                lines.append("")
    
    lines.append("-" * 80)
    lines.append("")
    
    # Focus of tension
    focus = data.get('focus_of_tension', {})
    lines.append("ОБЛАСТИ НАПРЯЖЕНИЯ")
    lines.append("")
    
    domains = focus.get('domains', [])
    if domains:
        lines.append("Домены:")
        domain_names = {
            'safety_and_protection': 'Безопасность и защита',
            'connection_and_belonging': 'Связь и принадлежность',
            'autonomy_and_control': 'Автономия и контроль',
            'change_and_uncertainty': 'Изменения и неопределённость',
            'identity_and_continuity': 'Идентичность и непрерывность',
            'meaning_and_purpose': 'Смысл и цель',
            'resource_management': 'Управление ресурсами'
        }
        for domain in domains:
            lines.append(f"  • {domain_names.get(domain, domain)}")
    
    indicators = focus.get('indicators', [])
    if indicators:
        lines.append("")
        lines.append("Индикаторы:")
        for ind in indicators:
            lines.append(f"  • {ind}")
    
    lines.append("")
    lines.append("-" * 80)
    lines.append("")
    
    # Compensatory patterns
    patterns = data.get('compensatory_patterns', [])
    if patterns:
        lines.append("КОМПЕНСАТОРНЫЕ ПАТТЕРНЫ")
        lines.append("")
        
        pattern_names = {
            'distancing': 'Дистанцирование',
            'control_seeking': 'Поиск контроля',
            'symbolic_repair': 'Символическое восстановление',
            'affect_modulation': 'Модуляция аффекта',
            'fragmentation': 'Фрагментация',
            'idealization': 'Идеализация',
            'externalization': 'Экстернализация',
            'other': 'Другое'
        }
        
        for patt in patterns:
            patt_type = patt.get('pattern', 'N/A')
            evidence = patt.get('evidence', '')
            confidence = patt.get('confidence', 'N/A')
            
            lines.append(f"• {pattern_names.get(patt_type, patt_type)} ({confidence})")
            lines.append(f"  {evidence}")
            lines.append("")
        
        lines.append("-" * 80)
        lines.append("")
    
    # Uncertainty profile
    uncertainty = data.get('uncertainty_profile', {})
    lines.append("ПРОФИЛЬ НЕОПРЕДЕЛЁННОСТИ")
    lines.append("")
    lines.append(f"Общая уверенность: {uncertainty.get('overall_confidence', 'N/A').upper()}")
    lines.append("")
    
    data_gaps = uncertainty.get('data_gaps', [])
    if data_gaps:
        lines.append("Недостающие данные:")
        for gap in data_gaps:
            lines.append(f"  • {gap}")
        lines.append("")
    
    ambiguities = uncertainty.get('ambiguities', [])
    if ambiguities:
        lines.append("Неоднозначности:")
        for amb in ambiguities:
            lines.append(f"  • {amb}")
        lines.append("")
    
    cautions = uncertainty.get('cautions', [])
    if cautions:
        lines.append("Предостережения:")
        for caut in cautions:
            lines.append(f"  • {caut}")
        lines.append("")
    
    lines.append("-" * 80)
    lines.append("")
    
    # Clarification directions
    directions = data.get('clarification_directions', [])
    if directions:
        lines.append("НАПРАВЛЕНИЯ ДЛЯ УТОЧНЕНИЯ")
        lines.append("")
        
        for direction in directions:
            dir_text = direction.get('direction', '')
            rationale = direction.get('rationale', '')
            priority = direction.get('priority', 'medium')
            
            lines.append(f"[{priority.upper()}] {dir_text}")
            if rationale:
                lines.append(f"  Обоснование: {rationale}")
            lines.append("")
    
    # Footer
    lines.append("=" * 80)
    lines.append("Конец отчёта")
    lines.append("=" * 80)
    
    # Write to file
    content = "\n".join(lines)
    output_path.write_text(content, encoding='utf-8')
    
    return str(output_path)


if __name__ == '__main__':
    # Test with minimal valid JSON
    test_data = {
        'meta': {
            'session_id': 'test_123',
            'timestamp': '2026-02-06T12:00:00Z',
            'state': 'INTERPRETATION_GENERATION',
            'mode': 'STANDARD',
            'iteration_count': 1
        },
        'input_summary': {
            'material_type': 'dream',
            'source': 'client_report',
            'completeness': 'sufficient',
            'clarifications_received': []
        },
        'phenomenological_summary': {
            'text': 'Test summary',
            'key_elements': []
        },
        'interpretative_hypotheses': [],
        'focus_of_tension': {
            'domains': [],
            'indicators': []
        },
        'compensatory_patterns': [],
        'uncertainty_profile': {
            'overall_confidence': 'low',
            'data_gaps': ['Test gap'],
            'ambiguities': ['Test ambiguity'],
            'cautions': []
        },
        'clarification_directions': [],
        'policy_flags': {
            'hypothesis_count': 0,
            'contains_diagnosis': False,
            'contains_trauma_claim': False,
            'contains_pathology_language': False,
            'contains_psycheos_terms': False,
            'uncertainty_present': True,
            'repair_applied': False,
            'violations': []
        }
    }
    
    valid, errors = validate_structured_results(test_data)
    print(f"✓ Structured Results module loaded")
    print(f"  Validation: {'PASS' if valid else 'FAIL'}")
    if errors:
        print(f"  Errors: {errors}")
