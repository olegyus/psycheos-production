"""Output models for conceptualization."""

from typing import List, Optional
from pydantic import BaseModel
from core.enums import PsycheLevelEnum, HypothesisType


class LayerA(BaseModel):
    """Layer A: Conceptual Model (System Mode)."""
    leading_formulation: str
    supporting_points: List[str]
    dominant_layer: PsycheLevelEnum
    configuration_summary: str
    system_cost: str


class InterventionTarget(BaseModel):
    """Single intervention target."""
    layer: str
    direction: str
    priority: int
    rationale: str


class LayerB(BaseModel):
    """Layer B: Intervention Targets (Managerial Mode)."""
    targets: List[InterventionTarget]
    sequencing_notes: str


class LayerC(BaseModel):
    """Layer C: Metaphorical Narrative (Client Mode)."""
    core_metaphor: str
    narrative: str
    direction_of_change: str


class ConceptualizationOutput(BaseModel):
    """Complete three-layer output."""
    session_id: str
    layer_a: LayerA
    layer_b: LayerB
    layer_c: LayerC
