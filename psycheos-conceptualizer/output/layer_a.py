"""Layer A Assembly - Conceptual Model."""

from typing import List
from core.models import SessionState
from core.enums import PsycheLevelEnum, HypothesisType
from .models import LayerA


def assemble_layer_a(session: SessionState) -> LayerA:
    """Assemble Layer A: Conceptual Model."""
    
    hypotheses = session.get_active_hypotheses()
    
    # Get leading hypothesis (prefer structural)
    structural = [h for h in hypotheses if h.type == HypothesisType.STRUCTURAL]
    leading = structural[0] if structural else hypotheses[0]
    
    # Get supporting points
    supporting_points = []
    for hyp in hypotheses[:4]:  # Max 4
        if hyp.id != leading.id:
            supporting_points.append(f"{hyp.type.value}: {hyp.formulation}")
    
    # Determine dominant layer
    layer_counts = {}
    for hyp in hypotheses:
        for level in hyp.levels:
            layer_counts[level] = layer_counts.get(level, 0) + 1
    
    dominant_layer = max(layer_counts.items(), key=lambda x: x[1])[0] if layer_counts else PsycheLevelEnum.L0
    
    # Generate configuration summary
    config_summary = (
        f"Система конфигурирована с {leading.formulation.lower()}. "
        f"Максимальное напряжение на уровне {dominant_layer.value}. "
        f"Паттерн поддерживается через петли обратной связи, где краткосрочная стабилизация "
        f"предотвращает обучение альтернативным конфигурациям."
    )
    
    # System cost
    system_cost = (
        f"Энергетическая цена: истощение ресурсов L0. "
        f"Социальная цена: ограничение контактов на L3. "
        f"Семантическая цена: сужение идентичности на L4."
    )
    
    return LayerA(
        leading_formulation=leading.formulation,
        supporting_points=supporting_points,
        dominant_layer=dominant_layer,
        configuration_summary=config_summary,
        system_cost=system_cost
    )
