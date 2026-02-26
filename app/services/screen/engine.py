"""Screen v2 vector engine.

Stateless computations: aggregation, tanh normalization, tension matrix,
rigidity index, confidence score, ambiguity zones, dominant cells.
"""
import math
from collections import Counter

AXES = ["A1", "A2", "A3", "A4"]
LAYERS = ["L0", "L1", "L2", "L3", "L4"]

_AMBIGUITY_THRESHOLD = 0.1        # |M[Lk,Aj]| below this → ambiguous cell
_POLARIZATION_THRESHOLD = 0.7     # |axis_score| above this → polarized
_LOW_VARIANCE_STD_REF = 0.3       # std reference for low-variance normalization
_STABILITY_STD_REF = 0.5          # std reference for confidence stability


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

        raw_axis = {
            a: sum(r.get("axis_weights", {}).get(a, 0.0) for r in responses) / n
            for a in AXES
        }
        raw_layer = {
            l: sum(r.get("layer_weights", {}).get(l, 0.0) for r in responses) / n
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
