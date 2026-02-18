"""Layer B Assembly using Claude API."""

import json
import logging
from pathlib import Path

from core.models import SessionState
from llm_service import get_claude_service
from .models import LayerB, InterventionTarget

logger = logging.getLogger(__name__)


def load_output_prompt(filename: str) -> str:
    """Load output prompt from file."""
    prompt_path = Path("prompts/output") / filename
    return prompt_path.read_text(encoding="utf-8")


def assemble_layer_b(session: SessionState) -> LayerB:
    """Assemble Layer B using Claude."""
    
    claude = get_claude_service()
    system_prompt = load_output_prompt("layer_b.txt")
    
    # Get managerial hypotheses
    managerial_hyps = session.get_managerial_hypotheses()
    
    if not managerial_hyps:
        raise ValueError("No managerial hypotheses for Layer B")
    
    # Prepare context
    context = "# Управленческие гипотезы:\n\n"
    for hyp in managerial_hyps:
        levels_str = ", ".join([l.value for l in hyp.levels])
        context += f"[{levels_str}] {hyp.formulation}\n\n"
    
    user_message = f"""
{context}

На основе этих управленческих гипотез создай Layer B - мишени вмешательства.

КРИТИЧЕСКИ ВАЖНО:
- Direction = ЧТО должно измениться, НЕ описание паттерна!
- Формулировки конкретные и actionable
- Приоритеты: L0 = 1-2, L4 = 4-5
"""
    
    try:
        response = claude.generate(
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=0.7
        )
        
        # Parse JSON
        response_clean = response.strip()
        if response_clean.startswith("```json"):
            response_clean = response_clean[7:]
        if response_clean.startswith("```"):
            response_clean = response_clean[3:]
        if response_clean.endswith("```"):
            response_clean = response_clean[:-3]
        response_clean = response_clean.strip()
        
        data = json.loads(response_clean)
        
        # Create targets
        targets = []
        for target_data in data["targets"]:
            target = InterventionTarget(
                layer=target_data["layer"],
                direction=target_data["direction"],
                priority=target_data["priority"],
                rationale=target_data["rationale"]
            )
            targets.append(target)
        
        layer_b = LayerB(
            targets=targets,
            sequencing_notes=data["sequencing_notes"]
        )
        
        logger.info(f"Layer B assembled via Claude. Targets: {len(targets)}")
        
        return layer_b
        
    except Exception as e:
        logger.error(f"Error assembling Layer B: {e}", exc_info=True)
        raise
