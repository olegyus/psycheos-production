"""Analysis module using Claude API."""

import json
import logging
from typing import Dict, Any
from pathlib import Path

from core.models import SessionState, Hypothesis
from core.enums import HypothesisType, PsycheLevelEnum, ConfidenceLevel
from llm_service import get_claude_service

logger = logging.getLogger(__name__)


def load_prompt(filename: str) -> str:
    """Load prompt from file."""
    prompt_path = Path("prompts/analysis") / filename
    
    if not prompt_path.exists():
        logger.warning(f"Prompt file not found: {prompt_path}")
        return ""
    
    return prompt_path.read_text(encoding="utf-8")


def post_process_hypothesis_type(formulation: str, extracted_type: str) -> str:
    """
    Post-process hypothesis type with rule-based override.
    
    If formulation contains strong managerial markers, override to managerial.
    """
    formulation_lower = formulation.lower()
    
    # Strong managerial markers
    managerial_markers = [
        "можно", "нужно", "стоит", "начать с", "вмешаться",
        "воздействовать", "влиять", "изменить", "скорректировать",
        "работать с", "фокус на", "приоритет", "критическая точка",
        "точка управления", "leverage"
    ]
    
    # Count managerial markers
    marker_count = sum(1 for marker in managerial_markers if marker in formulation_lower)
    
    # If 2+ markers present and extracted_type is not managerial - override
    if marker_count >= 2 and extracted_type != "managerial":
        logger.warning(
            f"Override: extracted '{extracted_type}' but found {marker_count} managerial markers. "
            f"Changing to 'managerial'."
        )
        return "managerial"
    
    return extracted_type


def extract_hypothesis_from_response(
    message: str, 
    session: SessionState
) -> Hypothesis:
    """
    Extract structured hypothesis from specialist's response using Claude.
    
    Args:
        message: Specialist's response
        session: Current session state
    
    Returns:
        Extracted Hypothesis
    """
    claude = get_claude_service()
    
    # Load system prompt
    system_prompt = load_prompt("extract_hypothesis.txt")
    
    # Prepare user message with context
    user_message = f"""
Контекст сессии:
- Текущих гипотез: {len(session.get_active_hypotheses())}
- Управленческих гипотез: {len(session.get_managerial_hypotheses())}
- Вопросов задано: {session.progress.dialogue_turns}

Ответ специалиста:
{message}

Извлеки структурированную гипотезу из этого ответа.
ВАЖНО: Если текст содержит слова "можно", "нужно", "стоит", "вмешаться" - это MANAGERIAL!
"""
    
    try:
        # Generate analysis
        response = claude.generate(
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=0.5  # Increased from 0.3
        )
        
        logger.info(f"Claude raw response: {response[:500]}")
        
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
        
        logger.info(f"Parsed data: {data}")
        
        # POST-PROCESS: Override type if needed
        original_type = data["type"]
        corrected_type = post_process_hypothesis_type(data["formulation"], original_type)
        
        if corrected_type != original_type:
            logger.info(f"Type corrected: {original_type} → {corrected_type}")
            data["type"] = corrected_type
        
        # Convert to Hypothesis
        hyp_type = HypothesisType(data["type"])
        levels = [PsycheLevelEnum(l) for l in data["levels"]]
        confidence = ConfidenceLevel(data["confidence"])
        
        hypothesis = Hypothesis(
            id=f"hyp_{session.progress.hypotheses_added + 1:03d}",
            type=hyp_type,
            levels=levels,
            formulation=data["formulation"],
            confidence=confidence,
            foundations=[data.get("reasoning", "")]
        )
        
        logger.info(
            f"Final hypothesis: {hyp_type.value} on {[l.value for l in levels]}"
        )
        
        return hypothesis
        
    except Exception as e:
        logger.error(f"Error extracting hypothesis: {e}", exc_info=True)
        
        # Fallback: simple hypothesis
        return Hypothesis(
            id=f"hyp_{session.progress.hypotheses_added + 1:03d}",
            type=HypothesisType.STRUCTURAL,
            levels=[PsycheLevelEnum.L0],
            formulation=message[:300],
            confidence=ConfidenceLevel.WEAK,
            foundations=["Fallback extraction"]
        )


__all__ = [
    "extract_hypothesis_from_response",
]
