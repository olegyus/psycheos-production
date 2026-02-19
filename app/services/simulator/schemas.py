"""Pydantic-модели данных PsycheOS Simulator v1.1.

Изменения v1.1:
  - defense_activation в HiddenState
  - IterationLog — структурированный лог каждой реплики
  - TSIComponents + TSIResult — Therapeutic Stability Index
  - CCI — Case Complexity Index (вычисляемый)
  - AlternativeTrajectory — блок альтернативных сценариев
  - SpecialistProfile — накопительный профиль специалиста
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, computed_field


# ═══════════════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════════════

class CrisisFlag(str, Enum):
    NONE = "NONE"
    MODERATE = "MODERATE"
    HIGH = "HIGH"


class SessionGoal(str, Enum):
    CONTACT_STABILIZATION = "CONTACT_STABILIZATION"
    DIAGNOSTIC_CLARIFICATION = "DIAGNOSTIC_CLARIFICATION"
    SYMPTOM_WORK = "SYMPTOM_WORK"
    REGULATORY_CONFLICT = "REGULATORY_CONFLICT"
    COGNITIVE_RESTRUCTURING = "COGNITIVE_RESTRUCTURING"
    AFFECT_WORK = "AFFECT_WORK"
    CRISIS_SUPPORT = "CRISIS_SUPPORT"
    THERAPY_TERMINATION = "THERAPY_TERMINATION"


class SessionMode(str, Enum):
    TRAINING = "TRAINING"
    PRACTICE = "PRACTICE"


class FSMState(str, Enum):
    S0_INIT = "S0"
    S1_CONTACT = "S1"
    S2_DIAGNOSTIC = "S2"
    S3_FOCUSED = "S3"
    S4_CRISIS = "S4"
    S5_STABILIZATION = "S5"
    S6_TERMINATION = "S6"
    S7_ANALYTICS = "S7"


class SignalType(str, Enum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


# ═══════════════════════════════════════════════════════════════════════════
# CASE DYNAMICS
# ═══════════════════════════════════════════════════════════════════════════

class CaseDynamics(BaseModel):
    """Параметры динамики — именно этим кейсы отличаются друг от друга."""
    baseline_tension_L0: int = Field(ge=0, le=100)
    baseline_cognitive_access: int = Field(ge=0, le=100)
    baseline_uncertainty: int = Field(ge=0, le=100)
    baseline_trust: int = Field(ge=0, le=100)

    L0_reactivity: str = Field(description="low / moderate / high")
    L2_strength: str = Field(description="low / moderate / high")
    L3_accessibility: str = Field(description="low / moderate / high")
    interpretation_tolerance: str = Field(description="low / moderate / high")
    uncertainty_tolerance: str = Field(description="low / moderate / high")
    cognitive_window: str = Field(description="narrow / moderate / wide")
    escalation_speed: str = Field(description="slow / moderate / fast")
    intervention_range: str = Field(description="narrow / moderate / wide")

    recovery_rate: float = Field(ge=0.0, le=1.0)
    volatility: float = Field(ge=0.0, le=1.0)


# ═══════════════════════════════════════════════════════════════════════════
# SCREEN PROFILE
# ═══════════════════════════════════════════════════════════════════════════

class ContinuumScore(BaseModel):
    value: int = Field(ge=0, le=100)
    variability: str = "moderate"
    rigidity: bool = False


class ScreenProfile(BaseModel):
    economy_exploration: ContinuumScore
    protection_contact: ContinuumScore
    retention_movement: ContinuumScore
    survival_development: ContinuumScore
    tension_zones: list[str] = Field(default_factory=list)
    rigidity_flags: list[str] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# LAYERS & CONCEPTUALIZATION
# ═══════════════════════════════════════════════════════════════════════════

class LayerDescription(BaseModel):
    description: str
    key_markers: list[str] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)


class Layers(BaseModel):
    L0: LayerDescription
    L1: LayerDescription
    L2: LayerDescription
    L3: LayerDescription
    L4: LayerDescription


class SystemCost(BaseModel):
    energetic: str = ""
    social: str = ""
    semantic: str = ""


class LayerA(BaseModel):
    leading_hypothesis: str
    dominant_layer: str
    configuration: str
    system_cost: SystemCost = Field(default_factory=SystemCost)


class Target(BaseModel):
    level: str
    description: str


class LayerB(BaseModel):
    targets: list[Target]
    sequence: str
    transition_criterion: str = ""


class LayerC(BaseModel):
    metaphor: str = ""
    narrative: str = ""
    change_direction: str = ""


class Conceptualization(BaseModel):
    layer_a: LayerA
    layer_b: LayerB
    layer_c: Optional[LayerC] = None


# ═══════════════════════════════════════════════════════════════════════════
# CLIENT INFO
# ═══════════════════════════════════════════════════════════════════════════

class ClientInfo(BaseModel):
    id: str
    gender: str
    age: int
    presenting_complaints: list[str]


# ═══════════════════════════════════════════════════════════════════════════
# CCI — Case Complexity Index (v1.1)
# ═══════════════════════════════════════════════════════════════════════════

class CCIComponents(BaseModel):
    """Компоненты индекса сложности кейса."""
    baseline_L0: float = Field(ge=0.0, le=1.0, description="baseline_tension_L0 / 100")
    volatility: float = Field(ge=0.0, le=1.0, description="Частота скачков")
    layer_depth: float = Field(ge=0.0, le=1.0, description="Глубина доступных слоёв (инверсия L3_accessibility)")
    cascade_risk: float = Field(ge=0.0, le=1.0, description="Риск каскада")
    intervention_window: float = Field(ge=0.0, le=1.0, description="Инверсия ширины окна интервенций")

    @computed_field
    @property
    def cci(self) -> float:
        """CCI = средневзвешенное 5 компонентов."""
        return round(
            self.baseline_L0 * 0.25
            + self.volatility * 0.15
            + self.layer_depth * 0.20
            + self.cascade_risk * 0.25
            + self.intervention_window * 0.15,
            2,
        )


def compute_cci(dynamics: CaseDynamics) -> CCIComponents:
    """Вычисляет CCI из параметров динамики кейса."""
    _level = {"low": 0.25, "moderate": 0.5, "high": 0.75}
    _width = {"narrow": 0.75, "moderate": 0.5, "wide": 0.25}
    _speed = {"slow": 0.25, "moderate": 0.5, "fast": 0.75}

    l3_access = _level.get(dynamics.L3_accessibility, 0.5)
    int_window = _width.get(dynamics.intervention_range, 0.5)
    esc = _speed.get(dynamics.escalation_speed, 0.5)
    l0_react = _level.get(dynamics.L0_reactivity, 0.5)
    cascade = round((esc + l0_react) / 2, 2)

    return CCIComponents(
        baseline_L0=round(dynamics.baseline_tension_L0 / 100, 2),
        volatility=dynamics.volatility,
        layer_depth=round(1.0 - l3_access, 2),
        cascade_risk=cascade,
        intervention_window=int_window,
    )


# ═══════════════════════════════════════════════════════════════════════════
# BUILT-IN CASE
# ═══════════════════════════════════════════════════════════════════════════

class BuiltinCase(BaseModel):
    """Полный встроенный кейс."""
    case_id: str
    case_name: str
    difficulty: str

    client: ClientInfo
    screen_profile: ScreenProfile
    layers: Layers
    conceptualization: Conceptualization
    dynamics: CaseDynamics

    crisis_flag: CrisisFlag
    sensitive_zones: list[str] = Field(default_factory=list)
    predicted_error_trajectory: dict[str, str] = Field(default_factory=dict)

    @computed_field
    @property
    def cci(self) -> CCIComponents:
        """Автоматически вычисляемый индекс сложности."""
        return compute_cci(self.dynamics)


# ═══════════════════════════════════════════════════════════════════════════
# TSI — Therapeutic Stability Index (v1.1)
# ═══════════════════════════════════════════════════════════════════════════

class TSIComponents(BaseModel):
    """5 компонентов индекса устойчивости терапевтической позиции."""
    R_match: float = Field(ge=0.0, le=1.0, description="Соответствие активному уровню")
    L_consistency: float = Field(ge=0.0, le=1.0, description="Последовательность Layer B")
    Alliance_score: float = Field(ge=0.0, le=1.0, description="Динамика доверия и контакта")
    Uncertainty_modulation: float = Field(ge=0.0, le=1.0, description="Управление неопределённостью")
    Therapist_reactivity: float = Field(ge=0.0, le=1.0, description="Преждевременные вмешательства (ниже = лучше)")

    @computed_field
    @property
    def tsi(self) -> float:
        """TSI = взвешенная сумма."""
        return round(
            self.R_match * 0.25
            + self.L_consistency * 0.20
            + self.Alliance_score * 0.20
            + self.Uncertainty_modulation * 0.20
            + (1.0 - self.Therapist_reactivity) * 0.15,
            2,
        )

    @computed_field
    @property
    def interpretation(self) -> str:
        """Текстовая интерпретация TSI."""
        t = self.tsi
        if t >= 0.85:
            return "высокая устойчивость"
        elif t >= 0.70:
            return "функциональная"
        elif t >= 0.50:
            return "нестабильная"
        else:
            return "риск каскада"


# ═══════════════════════════════════════════════════════════════════════════
# ITERATION LOG (v1.1) — структурированный лог каждой реплики
# ═══════════════════════════════════════════════════════════════════════════

class DeltaValues(BaseModel):
    """Изменение скрытых переменных за одну итерацию."""
    trust: int = 0
    tension_L0: int = 0
    uncertainty: int = 0
    defense_activation: int = 0
    cognitive_access: int = 0


class IterationLog(BaseModel):
    """Лог одной итерации (реплика специалиста → ответ клиента)."""
    replica_id: int
    specialist_input: str = ""
    fsm_before: str = ""
    fsm_after: str = ""
    active_layer_before: str = ""
    active_layer_after: str = ""
    signal: SignalType = SignalType.GREEN
    signal_reason: str = ""
    regulatory_match_score: float = Field(default=0.0, ge=0.0, le=1.0)
    delta: DeltaValues = Field(default_factory=DeltaValues)
    cascade_probability: float = Field(default=0.0, ge=0.0, le=1.0)
    crisis_warning: bool = False


# ═══════════════════════════════════════════════════════════════════════════
# ALTERNATIVE TRAJECTORY (v1.1)
# ═══════════════════════════════════════════════════════════════════════════

class AlternativeTrajectory(BaseModel):
    """Один альтернативный сценарий (что было бы при ошибке)."""
    risk_point_replica: int
    potential_error: str
    predicted_client_response: str = ""
    fsm_shift: str = ""
    delta_trust: int = 0
    delta_tension_L0: int = 0
    delta_uncertainty: int = 0
    cascade_probability: float = Field(default=0.0, ge=0.0, le=1.0)


# ═══════════════════════════════════════════════════════════════════════════
# SPECIALIST PROFILE (v1.1) — накопительный профиль
# ═══════════════════════════════════════════════════════════════════════════

class SpecialistProfile(BaseModel):
    """Накопительный профиль специалиста между сессиями."""
    specialist_id: str
    sessions_count: int = 0
    average_tsi: float = 0.0
    average_delta_trust: float = 0.0
    yellow_ratio: float = 0.0
    red_ratio: float = 0.0
    dominant_error_pattern: str = ""
    typical_jump_level: str = ""
    recommended_case_complexity: float = 0.6

    tsi_history: list[float] = Field(default_factory=list)
    cases_completed: list[str] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# SESSION STATE (runtime) — обновлено для v1.1
# ═══════════════════════════════════════════════════════════════════════════

class HiddenState(BaseModel):
    """Скрытые переменные Client Engine."""
    tension_L0: int = 40
    cognitive_access: int = 68
    uncertainty_index: int = 65
    trust_level: int = 30
    defense_activation: int = 40
    active_layer: str = "L0"


class SessionData(BaseModel):
    """Полное состояние одной активной сессии."""
    user_id: int
    case_id: str
    case_name: str
    mode: SessionMode
    session_goal: SessionGoal
    crisis_flag: CrisisFlag
    fsm_state: FSMState = FSMState.S1_CONTACT

    hidden_state: HiddenState = Field(default_factory=HiddenState)

    messages: list[dict] = Field(default_factory=list)

    signal_log: list[str] = Field(default_factory=list)
    fsm_log: list[str] = Field(default_factory=list)

    iteration_log: list[IterationLog] = Field(default_factory=list)

    tsi: Optional[TSIComponents] = None

    alternative_trajectories: list[AlternativeTrajectory] = Field(default_factory=list)

    active: bool = True
