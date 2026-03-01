"""Screen v2 vector engine.

Stateless computations: aggregation, tanh normalization, tension matrix,
rigidity index, confidence score, ambiguity zones, dominant cells.
"""
import math
from collections import Counter, defaultdict

AXES = ["A1", "A2", "A3", "A4"]
LAYERS = ["L0", "L1", "L2", "L3", "L4"]

_AMBIGUITY_THRESHOLD = 0.1        # |M[Lk,Aj]| below this → ambiguous cell
_POLARIZATION_THRESHOLD = 0.7     # |axis_score| above this → polarized
_LOW_VARIANCE_STD_REF = 0.3       # std reference for low-variance normalization
_STABILITY_STD_REF = 0.5          # std reference for confidence stability


def classify_axis_intensity(axis_vector: dict) -> dict:
    """Classify intensity of each axis score.

    Returns:
        {axis: "extreme" | "high" | "moderate" | "low"}
    """
    result = {}
    for axis, value in axis_vector.items():
        abs_val = abs(value)
        if abs_val >= 0.9:
            result[axis] = "extreme"
        elif abs_val >= 0.75:
            result[axis] = "high"
        elif abs_val >= 0.5:
            result[axis] = "moderate"
        else:
            result[axis] = "low"
    return result


def detect_regulatory_compression(axis_vector: dict) -> dict:
    """Detect structural compression patterns in the axis vector.

    Compression features detected:
        uncertainty_avoidance_extreme — A2 <= -0.9
        future_horizon_constricted   — A4 <= -0.9
        reduced_activation           — A1 <= -0.5

    Returns:
        {"compressed": bool, "features": list[str]}
    Compressed is True when at least two features are present.
    """
    features: list[str] = []
    if axis_vector.get("A2", 0.0) <= -0.9:
        features.append("uncertainty_avoidance_extreme")
    if axis_vector.get("A4", 0.0) <= -0.9:
        features.append("future_horizon_constricted")
    if axis_vector.get("A1", 0.0) <= -0.5:
        features.append("reduced_activation")
    return {
        "compressed": len(features) >= 2,
        "features": features,
    }


def detect_vertical_dominance(axis_vector: dict, dominant_cells: list) -> dict:
    """Detect structural dominance and vertical integration across layers.

    Returns:
        {
            "axis": str | None,          — axis key with highest |score|, or None
            "is_dominant": bool,         — True if |score| >= 0.7
            "is_vertical_integrated": bool — True if dominant axis appears in >=3 dominant cells
        }
    """
    dominant_axis = None
    is_dominant = False
    is_vertical_integrated = False

    if axis_vector:
        max_axis = max(axis_vector.items(), key=lambda x: abs(x[1]))
        axis_name, axis_value = max_axis
        if abs(axis_value) >= 0.7:
            dominant_axis = axis_name
            is_dominant = True
            count = sum(1 for cell in dominant_cells if axis_name in cell)
            if count >= 3:
                is_vertical_integrated = True

    return {
        "axis": dominant_axis,
        "is_dominant": is_dominant,
        "is_vertical_integrated": is_vertical_integrated,
    }


def analyze_horizontal_coherence(tension_matrix: dict) -> dict:
    """Evaluate horizontal organization per layer.

    Returns a dict mapping each layer key to one of:
        "polarized"   — single strong signal (|max| >= 0.7)
        "conflictive" — strong opposing signals (both > 0.6 and < -0.6)
        "coherent"    — no strong signals
    """
    layer_values: dict[str, list[float]] = defaultdict(list)
    for key, value in tension_matrix.items():
        layer, _ = key.split("_")
        layer_values[layer].append(value)

    result = {}
    for layer, values in layer_values.items():
        max_val = max(values, key=lambda x: abs(x))
        strong_positive = any(v > 0.6 for v in values)
        strong_negative = any(v < -0.6 for v in values)
        if strong_positive and strong_negative:
            result[layer] = "conflictive"
        elif abs(max_val) >= 0.7:
            result[layer] = "polarized"
        else:
            result[layer] = "coherent"
    return result


class ScreeningEngine:
    """Stateless vector computation engine for Screen v2 screening assessments."""

    @staticmethod
    def aggregate_vectors(responses: list[dict]) -> tuple[dict, dict]:
        """Average axis/layer weights across all responses, then apply tanh.

        Each response dict must contain:
            axis_weights:  {A1: float, A2: float, A3: float, A4: float}
            layer_weights: {L0: float, L1: float, L2: float, L3: float, L4: float}

        Returns:
            (axis_vector, layer_vector) — both dicts with tanh-normalised values.
        """
        if not responses:
            return ({a: 0.0 for a in AXES}, {l: 0.0 for l in LAYERS})

        n = len(responses)
        scale = math.sqrt(n)

        raw_axis = {
            a: sum(r.get("axis_weights", {}).get(a, 0.0) for r in responses) / scale
            for a in AXES
        }
        raw_layer = {
            l: sum(r.get("layer_weights", {}).get(l, 0.0) for r in responses) / scale
            for l in LAYERS
        }

        axis_vector = {a: math.tanh(v) for a, v in raw_axis.items()}
        layer_vector = {l: math.tanh(v) for l, v in raw_layer.items()}

        return axis_vector, layer_vector

    @staticmethod
    def compute_tension_matrix(axis_vector: dict, layer_vector: dict) -> dict:
        """Compute tension matrix M[Lk, Aj] = LayerScore_k * AxisScore_j.

        Returns dict with keys "L{k}_A{j}" for k in 0..4, j in 1..4  (20 cells).
        """
        matrix = {}
        for k in range(5):
            for j in range(1, 5):
                key = f"L{k}_A{j}"
                matrix[key] = layer_vector.get(f"L{k}", 0.0) * axis_vector.get(f"A{j}", 0.0)
        return matrix

    @staticmethod
    def compute_rigidity(responses: list[dict], axis_vector: dict) -> dict:
        """Compute rigidity index from response history and normalised axis vector.

        Components:
        - polarization:         fraction of axes with |score| > 0.7
        - low_variance:         1 - normalised avg std of per-axis contributions
                                (high when responses are consistent → rigid)
        - strategy_repetition:  fraction of responses sharing the dominant
                                positive/negative sign pattern across all axes

        Total = 0.3 * polarization + 0.3 * low_variance + 0.4 * strategy_repetition
        """
        if not responses:
            return {
                "polarization": 0.0,
                "low_variance": 0.0,
                "strategy_repetition": 0.0,
                "total": 0.0,
            }

        n = len(responses)

        # Polarization
        polarization = (
            sum(1 for a in AXES if abs(axis_vector.get(a, 0.0)) > _POLARIZATION_THRESHOLD)
            / len(AXES)
        )

        # Low variance — low std across responses per axis means rigid behaviour
        stds: list[float] = []
        for a in AXES:
            weights = [r.get("axis_weights", {}).get(a, 0.0) for r in responses]
            mean_w = sum(weights) / n
            variance = sum((w - mean_w) ** 2 for w in weights) / n
            stds.append(math.sqrt(variance))
        avg_std = sum(stds) / len(stds)
        low_variance = max(0.0, min(1.0, 1.0 - avg_std / _LOW_VARIANCE_STD_REF))

        # Strategy repetition — dominant sign pattern frequency
        patterns = []
        for r in responses:
            weights = r.get("axis_weights", {})
            pattern = tuple(1 if weights.get(a, 0.0) >= 0 else -1 for a in AXES)
            patterns.append(pattern)
        most_common_count = Counter(patterns).most_common(1)[0][1]
        strategy_repetition = most_common_count / n

        total = 0.3 * polarization + 0.3 * low_variance + 0.4 * strategy_repetition

        return {
            "polarization": polarization,
            "low_variance": low_variance,
            "strategy_repetition": strategy_repetition,
            "total": min(1.0, total),
        }

    @staticmethod
    def compute_confidence(
        responses: list[dict],
        axis_vector: dict,
        ambiguity_count: int,
    ) -> float:
        """Compute confidence score in [0, 1].

        Three equally-weighted components:
        - coverage:   fraction of axes with meaningful signal (|score| > 0.2)
        - stability:  1 - normalised avg std of per-axis contributions
        - clarity:    1 - fraction of ambiguous cells out of all 20 cells
        """
        if not responses:
            return 0.0

        n = len(responses)

        coverage = (
            sum(1 for a in AXES if abs(axis_vector.get(a, 0.0)) > 0.2) / len(AXES)
        )

        stds: list[float] = []
        for a in AXES:
            weights = [r.get("axis_weights", {}).get(a, 0.0) for r in responses]
            mean_w = sum(weights) / n
            variance = sum((w - mean_w) ** 2 for w in weights) / n
            stds.append(math.sqrt(variance))
        avg_std = sum(stds) / len(stds)
        stability = max(0.0, min(1.0, 1.0 - avg_std / _STABILITY_STD_REF))

        max_cells = len(AXES) * len(LAYERS)  # 20
        clarity = max(0.0, min(1.0, 1.0 - ambiguity_count / max_cells))

        return min(1.0, max(0.0, (coverage + stability + clarity) / 3.0))

    @staticmethod
    def find_ambiguity_zones(
        axis_vector: dict,
        layer_vector: dict,
        tension_matrix: dict,
    ) -> list[str]:
        """Return cell keys where |M[Lk,Aj]| < threshold (weak signal).

        Output format: "A{j}_L{k}" (axis first, then layer).
        """
        zones = []
        for key, value in tension_matrix.items():
            if abs(value) < _AMBIGUITY_THRESHOLD:
                # key is "L{k}_A{j}" → reformat to "A{j}_L{k}"
                lpart, apart = key.split("_")
                zones.append(f"{apart}_{lpart}")
        return zones

    @staticmethod
    def get_dominant_cells(tension_matrix: dict, top_n: int = 3) -> list[str]:
        """Return top N cell keys sorted by descending |M[Lk,Aj]|."""
        sorted_cells = sorted(
            tension_matrix.items(), key=lambda kv: abs(kv[1]), reverse=True
        )
        return [cell for cell, _ in sorted_cells[:top_n]]

    @classmethod
    def process_response(cls, current_state: dict, new_response: dict) -> dict:
        """Append new_response to state and recompute all derived metrics.

        Args:
            current_state: dict that may contain a "response_history" list.
            new_response:  dict with axis_weights and layer_weights.

        Returns:
            Updated state dict with keys:
                response_history, axis_vector, layer_vector, tension_matrix,
                ambiguity_zones, rigidity, confidence, dominant_cells.
        """
        responses = list(current_state.get("response_history", []))
        responses.append(new_response)

        axis_vector, layer_vector = cls.aggregate_vectors(responses)
        tension_matrix = cls.compute_tension_matrix(axis_vector, layer_vector)
        ambiguity_zones = cls.find_ambiguity_zones(axis_vector, layer_vector, tension_matrix)
        rigidity = cls.compute_rigidity(responses, axis_vector)
        confidence = cls.compute_confidence(responses, axis_vector, len(ambiguity_zones))
        dominant_cells = cls.get_dominant_cells(tension_matrix)

        return {
            "response_history": responses,
            "axis_vector": axis_vector,
            "layer_vector": layer_vector,
            "tension_matrix": tension_matrix,
            "ambiguity_zones": ambiguity_zones,
            "rigidity": rigidity,
            "confidence": confidence,
            "dominant_cells": dominant_cells,
        }
