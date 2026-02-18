"""Layer C Assembly using Claude API."""

import json
import logging
from pathlib import Path

from core.models import SessionState
from llm_service import get_claude_service
from .models import LayerC

logger = logging.getLogger(__name__)


def load_output_prompt(filename: str) -> str:
    """Load output prompt from file."""
    prompt_path = Path("prompts/output") / filename
    return prompt_path.read_text(encoding="utf-8")


def assemble_layer_c(session: SessionState) -> LayerC:
    """Assemble Layer C using Claude."""
    
    claude = get_claude_service()
    system_prompt = load_output_prompt("layer_c.txt")
    
    # Get all hypotheses for context
    hypotheses = session.get_active_hypotheses()
    
    context = "# Гипотезы для понимания конфликта:\n\n"
    for hyp in hypotheses:
        context += f"{hyp.type.value}: {hyp.formulation}\n\n"
    
    user_message = f"""
{context}

На основе этого понимания создай Layer C - метафорический нарратив для клиента.

КРИТИЧЕСКИ ВАЖНО:
- Метафора должна схватывать УПРАВЛЯЮЩИЙ КОНФЛИКТ, не симптом
- Нарратив на языке ОПЫТА, без L0-L4, гипотез, диагнозов
- Клиент должен узнать себя
"""
    
    try:
        response = claude.generate(
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=0.8  # Higher for creativity
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
        
        layer_c = LayerC(
            core_metaphor=data["core_metaphor"],
            narrative=data["narrative"],
            direction_of_change=data["direction_of_change"]
        )
        
        logger.info(f"Layer C assembled via Claude. Metaphor: {layer_c.core_metaphor}")
        
        return layer_c
        
    except Exception as e:
        logger.error(f"Error assembling Layer C: {e}", exc_info=True)
        raise
