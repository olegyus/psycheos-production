"""Output Assembly module with Claude integration."""

from core.models import SessionState
from .models import ConceptualizationOutput
from .layer_a_llm import assemble_layer_a
from .layer_b_llm import assemble_layer_b
from .layer_c_llm import assemble_layer_c


def assemble_output(session: SessionState) -> ConceptualizationOutput:
    """
    Assemble complete three-layer output using Claude API.
    
    Args:
        session: Current session state
    
    Returns:
        Complete conceptualization output
    
    Raises:
        ValueError: If session not ready for output
    """
    
    if not session.can_proceed_to_output():
        raise ValueError("Session not ready for output assembly")
    
    # Assemble each layer via Claude
    layer_a = assemble_layer_a(session)
    layer_b = assemble_layer_b(session)
    layer_c = assemble_layer_c(session)
    
    return ConceptualizationOutput(
        session_id=session.session_id,
        layer_a=layer_a,
        layer_b=layer_b,
        layer_c=layer_c
    )


__all__ = [
    "ConceptualizationOutput",
    "assemble_output",
]
