"""Enumerations for PsycheOS Conceptualizer."""

from enum import Enum


class SessionStateEnum(str, Enum):
    """Session states."""
    INIT = "init"
    DATA_COLLECTION = "data_collection"
    ANALYSIS = "analysis"
    SOCRATIC_DIALOGUE = "socratic_dialogue"
    OUTPUT_ASSEMBLY = "output_assembly"
    COMPLETE = "complete"


class PsycheLevelEnum(str, Enum):
    """PsycheOS layers."""
    L0 = "L0"  # Basic Regulation
    L1 = "L1"  # Reflexive Control
    L2 = "L2"  # Conscious Choice
    L3 = "L3"  # Social/Role Control
    L4 = "L4"  # Meanings/Identity


class HypothesisType(str, Enum):
    """Hypothesis types."""
    STRUCTURAL = "structural"
    FUNCTIONAL = "functional"
    DYNAMIC = "dynamic"
    MANAGERIAL = "managerial"


class ConfidenceLevel(str, Enum):
    """Confidence in hypothesis."""
    WEAK = "weak"
    WORKING = "working"
    DOMINANT = "dominant"
    CONDITIONAL = "conditional"


class QuestionType(str, Enum):
    """Question types for dialogue."""
    LEVEL_CHECK = "level_check"
    FUNCTION_CHECK = "function_check"
    DYNAMICS_CHECK = "dynamics_check"
    ALTERNATIVES_CHECK = "alternatives_check"
    CONTROL_CHECK = "control_check"
    OTHER = "other"


class TensionLevel(str, Enum):
    """Tension level in layer."""
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class DataDensity(str, Enum):
    """Data density for layer."""
    SPARSE = "sparse"
    MODERATE = "moderate"
    RICH = "rich"


class RedFlagType(str, Enum):
    """Red flag categories."""
    ARCHITECTURAL = "architectural"
    CLINICAL = "clinical"
    PROCEDURAL = "procedural"


class RedFlagSeverity(str, Enum):
    """Red flag severity."""
    WARNING = "warning"
    STOP = "stop"
    CRITICAL = "critical"
