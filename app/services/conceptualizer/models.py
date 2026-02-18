"""Core Pydantic models for PsycheOS Conceptualizer (production version)."""
from datetime import datetime, timezone
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from .enums import (
    ConfidenceLevel,
    DataDensity,
    HypothesisType,
    PsycheLevelEnum,
    RedFlagSeverity,
    RedFlagType,
    SessionStateEnum,
    TensionLevel,
)


class Hypothesis(BaseModel):
    id: str
    type: HypothesisType
    levels: List[PsycheLevelEnum]
    formulation: str
    confidence: ConfidenceLevel = ConfidenceLevel.WEAK
    foundations: List[str] = Field(default_factory=list)
    function: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class LayerData(BaseModel):
    markers: List[str] = Field(default_factory=list)
    tension_level: TensionLevel = TensionLevel.MODERATE
    data_density: DataDensity = DataDensity.SPARSE


class DataMap(BaseModel):
    layers: Dict[str, LayerData] = Field(default_factory=dict)
    specialist_observations: Optional[str] = None


class Progress(BaseModel):
    dialogue_turns: int = 0
    hypotheses_added: int = 0
    data_collection_complete: bool = False
    analysis_complete: bool = False

    def increment_dialogue_turns(self) -> None:
        self.dialogue_turns += 1


class RedFlag(BaseModel):
    type: RedFlagType
    severity: RedFlagSeverity
    description: str
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SessionState(BaseModel):
    session_id: str
    specialist_id: str
    state: SessionStateEnum = SessionStateEnum.INIT
    hypotheses: List[Hypothesis] = Field(default_factory=list)
    data_map: Optional[DataMap] = None
    progress: Progress = Field(default_factory=Progress)
    red_flags: List[RedFlag] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def add_hypothesis(self, hypothesis: Hypothesis) -> None:
        self.hypotheses.append(hypothesis)
        self.progress.hypotheses_added += 1
        self.updated_at = datetime.now(timezone.utc)

    def get_active_hypotheses(self) -> List[Hypothesis]:
        return self.hypotheses

    def get_managerial_hypotheses(self) -> List[Hypothesis]:
        return [h for h in self.hypotheses if h.type == HypothesisType.MANAGERIAL]

    def has_blocking_flags(self) -> bool:
        return any(
            f.severity in [RedFlagSeverity.STOP, RedFlagSeverity.CRITICAL]
            for f in self.red_flags
        )

    def get_blocking_red_flags(self) -> List[RedFlag]:
        return [
            f for f in self.red_flags
            if f.severity in [RedFlagSeverity.STOP, RedFlagSeverity.CRITICAL]
        ]

    def can_proceed_to_output(self) -> bool:
        if len(self.hypotheses) < 2:
            return False
        if not self.get_managerial_hypotheses():
            return False
        if self.has_blocking_flags():
            return False
        return True

    def transition_to(self, new_state: SessionStateEnum) -> None:
        valid_transitions = {
            SessionStateEnum.INIT: [SessionStateEnum.DATA_COLLECTION],
            SessionStateEnum.DATA_COLLECTION: [SessionStateEnum.ANALYSIS],
            SessionStateEnum.ANALYSIS: [SessionStateEnum.SOCRATIC_DIALOGUE],
            SessionStateEnum.SOCRATIC_DIALOGUE: [SessionStateEnum.OUTPUT_ASSEMBLY],
            SessionStateEnum.OUTPUT_ASSEMBLY: [SessionStateEnum.COMPLETE],
        }
        allowed = valid_transitions.get(self.state, [])
        if new_state not in allowed:
            raise ValueError(f"Cannot transition from {self.state} to {new_state}")
        self.state = new_state
        self.updated_at = datetime.now(timezone.utc)


# ── Output models ─────────────────────────────────────────────────────────────

class LayerA(BaseModel):
    leading_formulation: str
    supporting_points: List[str]
    dominant_layer: PsycheLevelEnum
    configuration_summary: str
    system_cost: str


class InterventionTarget(BaseModel):
    layer: str
    direction: str
    priority: int
    rationale: str


class LayerB(BaseModel):
    targets: List[InterventionTarget]
    sequencing_notes: str


class LayerC(BaseModel):
    core_metaphor: str
    narrative: str
    direction_of_change: str


class ConceptualizationOutput(BaseModel):
    session_id: str
    layer_a: LayerA
    layer_b: LayerB
    layer_c: LayerC
