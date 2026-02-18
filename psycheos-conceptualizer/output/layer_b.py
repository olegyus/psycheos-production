"""Layer B Assembly - Intervention Targets."""

from typing import List
from core.models import SessionState
from core.enums import HypothesisType
from .models import LayerB, InterventionTarget


def assemble_layer_b(session: SessionState) -> LayerB:
    """Assemble Layer B: Intervention Targets."""
    
    managerial_hyps = session.get_managerial_hypotheses()
    
    if not managerial_hyps:
        raise ValueError("No managerial hypotheses - cannot assemble Layer B")
    
    # Create targets from managerial hypotheses
    targets = []
    
    for i, hyp in enumerate(managerial_hyps[:5], 1):  # Max 5
        layer_str = ", ".join([l.value for l in hyp.levels])
        
        target = InterventionTarget(
            layer=layer_str,
            direction=hyp.formulation,
            priority=i,
            rationale=hyp.function if hyp.function else "Точка управления системой"
        )
        targets.append(target)
    
    # Sequencing notes
    sequencing = (
        "Рекомендуется последовательность снизу вверх: "
        "стабилизация L0 перед работой с L4. "
        "Фундамент перед надстройкой."
    )
    
    return LayerB(
        targets=targets,
        sequencing_notes=sequencing
    )
