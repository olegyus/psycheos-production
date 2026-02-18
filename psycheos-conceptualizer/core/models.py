"""Core Pydantic models for PsycheOS Conceptualizer."""

from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field
from .enums import (
    SessionStateEnum,
    HypothesisType,
    PsycheLevelEnum,
    ConfidenceLevel,
    TensionLevel,
    DataDensity,
    RedFlagType,
    RedFlagSeverity,
)


class Hypothesis(BaseModel):
    """Hypothesis model."""
    id: str
    type: HypothesisType
    levels: List[PsycheLevelEnum]
    formulation: str
    confidence: ConfidenceLevel = ConfidenceLevel.WEAK
    foundations: List[str] = Field(default_factory=list)
    function: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class LayerData(BaseModel):
    """Data for one psyche layer."""
    markers: List[str] = Field(default_factory=list)
    tension_level: TensionLevel = TensionLevel.MODERATE
    data_density: DataDensity = DataDensity.SPARSE


class DataMap(BaseModel):
    """Map of data across layers."""
    layers: Dict[PsycheLevelEnum, LayerData] = Field(default_factory=dict)
    specialist_observations: Optional[str] = None
    
    def get_high_tension_layers(self) -> List[PsycheLevelEnum]:
        """Get layers with high or critical tension."""
        return [
            level for level, data in self.layers.items()
            if data.tension_level in [TensionLevel.HIGH, TensionLevel.CRITICAL]
        ]


class Progress(BaseModel):
    """Session progress tracking."""
    dialogue_turns: int = 0
    hypotheses_added: int = 0
    data_collection_complete: bool = False
    analysis_complete: bool = False
    
    def increment_dialogue_turns(self) -> None:
        """Increment dialogue turn counter."""
        self.dialogue_turns += 1


class RedFlag(BaseModel):
    """Red flag indicator."""
    type: RedFlagType
    severity: RedFlagSeverity
    description: str
    detected_at: datetime = Field(default_factory=datetime.utcnow)


class SessionState(BaseModel):
    """Main session state model."""
    session_id: str
    specialist_id: str
    state: SessionStateEnum = SessionStateEnum.INIT
    hypotheses: List[Hypothesis] = Field(default_factory=list)
    data_map: Optional[DataMap] = None
    progress: Progress = Field(default_factory=Progress)
    red_flags: List[RedFlag] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    def add_hypothesis(self, hypothesis: Hypothesis) -> None:
        """Add hypothesis to session."""
        self.hypotheses.append(hypothesis)
        self.progress.hypotheses_added += 1
        self.updated_at = datetime.utcnow()
    
    def get_active_hypotheses(self) -> List[Hypothesis]:
        """Get all active hypotheses."""
        return self.hypotheses
    
    def get_managerial_hypotheses(self) -> List[Hypothesis]:
        """Get managerial hypotheses."""
        return [h for h in self.hypotheses if h.type == HypothesisType.MANAGERIAL]
    
    def add_red_flag(self, flag_type: RedFlagType, severity: RedFlagSeverity, description: str) -> None:
        """Add red flag."""
        flag = RedFlag(type=flag_type, severity=severity, description=description)
        self.red_flags.append(flag)
        self.updated_at = datetime.utcnow()
    
    def has_blocking_flags(self) -> bool:
        """Check if has blocking red flags."""
        return any(f.severity in [RedFlagSeverity.STOP, RedFlagSeverity.CRITICAL] 
                   for f in self.red_flags)
    
    def get_blocking_red_flags(self) -> List[RedFlag]:
        """Get blocking red flags."""
        return [f for f in self.red_flags 
                if f.severity in [RedFlagSeverity.STOP, RedFlagSeverity.CRITICAL]]
    
    def can_proceed_to_output(self) -> bool:
        """Check if session ready for output assembly."""
        # Need at least 2 hypotheses
        if len(self.hypotheses) < 2:
            return False
        
        # Need at least 1 managerial hypothesis
        if len(self.get_managerial_hypotheses()) < 1:
            return False
        
        # No blocking red flags
        if self.has_blocking_flags():
            return False
        
        return True
    
    def transition_to(self, new_state: SessionStateEnum) -> None:
        """Transition to new state."""
        # Valid transitions
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
        self.updated_at = datetime.utcnow()
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat(),
        }
