"""Layer A Assembly using Claude API."""

import json
import logging
from pathlib import Path
from typing import Dict, Any

from core.models import SessionState
from core.enums import PsycheLevelEnum
from llm_service import get_claude_service
from .models import LayerA

logger = logging.getLogger(__name__)


def load_output_prompt(filename: str) -> str:
    """Load output prompt from file."""
    prompt_path = Path("prompts/output") / filename
    return prompt_path.read_text(encoding="utf-8")


def prepare_hypotheses_context(session: SessionState) -> str:
    """Prepare hypotheses for context."""
    hypotheses = session.get_active_hypotheses()
    
    context = "# Гипотезы:\n\n"
    for hyp in hypotheses:
        levels_str = ", ".join([l.value for l in hyp.levels])
        context += f"**{hyp.type.value.upper()}** [{levels_str}]\n"
        context += f"{hyp.formulation}\n"
        context += f"Уверенность: {hyp.confidence.value}\n\n"
    
    return context


def assemble_layer_a(session: SessionState) -> LayerA:
    """Assemble Layer A using Claude."""
    
    claude = get_claude_service()
    system_prompt = load_output_prompt("layer_a.txt")
    
    # Prepare context
    hypotheses_context = prepare_hypotheses_context(session)
    
    user_message = f"""
{hypotheses_context}

На основе этих гипотез создай Layer A - техническую модель для специалиста.

КРИТИЧЕСКИ ВАЖНО:
1. Dominant layer - определи по УПРАВЛЯЮЩЕМУ КОНФЛИКТУ, не по частоте упоминаний
2. Configuration - покажи петли со СТРЕЛКАМИ (A→B→C), не абзацем
3. System cost - конкретная цена для L0, L3, L4
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
        
        # Extract supporting points (from non-leading hypotheses)
        hypotheses = session.get_active_hypotheses()
        supporting_points = []
        for hyp in hypotheses[:4]:
            if hyp.formulation != data["leading_formulation"]:
                supporting_points.append(f"{hyp.type.value}: {hyp.formulation[:100]}")
        
        # Format system cost
        cost_data = data["system_cost"]
        system_cost = (
            f"Энергетическая цена (L0): {cost_data['energetic']}\n"
            f"Социальная цена (L3): {cost_data['social']}\n"
            f"Семантическая цена (L4): {cost_data['semantic']}"
        )
        
        layer_a = LayerA(
            leading_formulation=data["leading_formulation"],
            supporting_points=supporting_points,
            dominant_layer=PsycheLevelEnum(data["dominant_layer"]),
            configuration_summary=data["configuration_summary"],
            system_cost=system_cost
        )
        
        logger.info(f"Layer A assembled via Claude. Dominant: {layer_a.dominant_layer.value}")
        
        return layer_a
        
    except Exception as e:
        logger.error(f"Error assembling Layer A: {e}", exc_info=True)
        raise
