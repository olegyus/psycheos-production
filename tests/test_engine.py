"""Tests for ScreeningEngine (Screen v2 vector engine)."""
import math

import pytest

from app.services.screen.engine import AXES, LAYERS, ScreeningEngine

# ---------------------------------------------------------------------------
# Test data — 14 responses designed to produce:
#   A1 < 0, A2 < 0, A3 > 0, A4 < 0
#   L4 > L2 > L0
# ---------------------------------------------------------------------------

TEST_RESPONSES = [
    {"axis_weights": {"A1": -0.5, "A2": -0.5, "A3": 0.3, "A4": -0.5},
     "layer_weights": {"L0": 0.20, "L1": -0.20, "L2": 0.40, "L3": -0.30, "L4": 0.60}},
    {"axis_weights": {"A1": -0.6, "A2": -0.4, "A3": 0.4, "A4": -0.6},
     "layer_weights": {"L0": 0.25, "L1": -0.10, "L2": 0.50, "L3": -0.25, "L4": 0.70}},
    {"axis_weights": {"A1": -0.4, "A2": -0.6, "A3": 0.3, "A4": -0.4},
     "layer_weights": {"L0": 0.15, "L1": -0.15, "L2": 0.35, "L3": -0.20, "L4": 0.55}},
    {"axis_weights": {"A1": -0.5, "A2": -0.5, "A3": 0.5, "A4": -0.5},
     "layer_weights": {"L0": 0.30, "L1": -0.20, "L2": 0.45, "L3": -0.30, "L4": 0.65}},
    {"axis_weights": {"A1": -0.7, "A2": -0.3, "A3": 0.3, "A4": -0.7},
     "layer_weights": {"L0": 0.20, "L1": -0.10, "L2": 0.40, "L3": -0.20, "L4": 0.60}},
    {"axis_weights": {"A1": -0.3, "A2": -0.7, "A3": 0.4, "A4": -0.3},
     "layer_weights": {"L0": 0.10, "L1": -0.20, "L2": 0.40, "L3": -0.10, "L4": 0.50}},
    {"axis_weights": {"A1": -0.5, "A2": -0.5, "A3": 0.3, "A4": -0.5},
     "layer_weights": {"L0": 0.20, "L1": -0.15, "L2": 0.50, "L3": -0.35, "L4": 0.70}},
    {"axis_weights": {"A1": -0.6, "A2": -0.6, "A3": 0.4, "A4": -0.6},
     "layer_weights": {"L0": 0.30, "L1": -0.10, "L2": 0.45, "L3": -0.25, "L4": 0.65}},
    {"axis_weights": {"A1": -0.4, "A2": -0.4, "A3": 0.5, "A4": -0.4},
     "layer_weights": {"L0": 0.25, "L1": -0.20, "L2": 0.40, "L3": -0.30, "L4": 0.60}},
    {"axis_weights": {"A1": -0.5, "A2": -0.5, "A3": 0.3, "A4": -0.5},
     "layer_weights": {"L0": 0.15, "L1": -0.15, "L2": 0.45, "L3": -0.20, "L4": 0.70}},
    {"axis_weights": {"A1": -0.6, "A2": -0.4, "A3": 0.4, "A4": -0.6},
     "layer_weights": {"L0": 0.20, "L1": -0.10, "L2": 0.50, "L3": -0.30, "L4": 0.65}},
    {"axis_weights": {"A1": -0.4, "A2": -0.6, "A3": 0.3, "A4": -0.4},
     "layer_weights": {"L0": 0.30, "L1": -0.20, "L2": 0.40, "L3": -0.25, "L4": 0.55}},
    {"axis_weights": {"A1": -0.5, "A2": -0.5, "A3": 0.5, "A4": -0.5},
     "layer_weights": {"L0": 0.20, "L1": -0.15, "L2": 0.45, "L3": -0.35, "L4": 0.75}},
    {"axis_weights": {"A1": -0.5, "A2": -0.5, "A3": 0.3, "A4": -0.5},
     "layer_weights": {"L0": 0.25, "L1": -0.20, "L2": 0.50, "L3": -0.30, "L4": 0.60}},
]

assert len(TEST_RESPONSES) == 14, "TEST_RESPONSES must contain exactly 14 items"


# ---------------------------------------------------------------------------
# aggregate_vectors
# ---------------------------------------------------------------------------

class TestAggregateVectors:
    def test_returns_correct_shape(self):
        axis_v, layer_v = ScreeningEngine.aggregate_vectors(TEST_RESPONSES)
        assert set(axis_v.keys()) == set(AXES)
        assert set(layer_v.keys()) == set(LAYERS)

    def test_axis_directions(self):
        """A1<0, A2<0, A3>0, A4<0 from the test dataset."""
        axis_v, _ = ScreeningEngine.aggregate_vectors(TEST_RESPONSES)
        assert axis_v["A1"] < 0, f"Expected A1<0, got {axis_v['A1']}"
        assert axis_v["A2"] < 0, f"Expected A2<0, got {axis_v['A2']}"
        assert axis_v["A3"] > 0, f"Expected A3>0, got {axis_v['A3']}"
        assert axis_v["A4"] < 0, f"Expected A4<0, got {axis_v['A4']}"

    def test_layer_ordering(self):
        """L4 > L2 > L0 from the test dataset."""
        _, layer_v = ScreeningEngine.aggregate_vectors(TEST_RESPONSES)
        assert layer_v["L4"] > layer_v["L2"], (
            f"Expected L4>L2, got L4={layer_v['L4']:.3f} L2={layer_v['L2']:.3f}"
        )
        assert layer_v["L2"] > layer_v["L0"], (
            f"Expected L2>L0, got L2={layer_v['L2']:.3f} L0={layer_v['L0']:.3f}"
        )

    def test_values_in_tanh_range(self):
        axis_v, layer_v = ScreeningEngine.aggregate_vectors(TEST_RESPONSES)
        for v in list(axis_v.values()) + list(layer_v.values()):
            assert -1.0 <= v <= 1.0

    def test_empty_responses(self):
        axis_v, layer_v = ScreeningEngine.aggregate_vectors([])
        assert all(v == 0.0 for v in axis_v.values())
        assert all(v == 0.0 for v in layer_v.values())

    def test_single_response_tanh(self):
        r = [{"axis_weights": {"A1": 1.0, "A2": 0.0, "A3": 0.0, "A4": 0.0},
              "layer_weights": {"L0": 0.0, "L1": 0.0, "L2": 0.0, "L3": 0.0, "L4": 0.0}}]
        axis_v, _ = ScreeningEngine.aggregate_vectors(r)
        assert abs(axis_v["A1"] - math.tanh(1.0)) < 1e-9


# ---------------------------------------------------------------------------
# compute_tension_matrix
# ---------------------------------------------------------------------------

class TestComputeTensionMatrix:
    def test_returns_20_cells(self):
        axis_v, layer_v = ScreeningEngine.aggregate_vectors(TEST_RESPONSES)
        m = ScreeningEngine.compute_tension_matrix(axis_v, layer_v)
        assert len(m) == 20

    def test_key_format(self):
        axis_v, layer_v = ScreeningEngine.aggregate_vectors(TEST_RESPONSES)
        m = ScreeningEngine.compute_tension_matrix(axis_v, layer_v)
        for key in m:
            lpart, apart = key.split("_")
            assert lpart.startswith("L") and lpart[1:].isdigit()
            assert apart.startswith("A") and apart[1:].isdigit()

    def test_cell_formula(self):
        axis_v = {"A1": 0.5, "A2": -0.3, "A3": 0.0, "A4": 0.8}
        layer_v = {"L0": 0.4, "L1": -0.2, "L2": 0.6, "L3": 0.1, "L4": -0.5}
        m = ScreeningEngine.compute_tension_matrix(axis_v, layer_v)
        assert abs(m["L0_A1"] - 0.4 * 0.5) < 1e-9
        assert abs(m["L4_A2"] - (-0.5) * (-0.3)) < 1e-9

    def test_zero_axis_gives_zero_row(self):
        axis_v = {"A1": 0.0, "A2": 0.0, "A3": 0.0, "A4": 0.0}
        layer_v = {"L0": 0.9, "L1": 0.9, "L2": 0.9, "L3": 0.9, "L4": 0.9}
        m = ScreeningEngine.compute_tension_matrix(axis_v, layer_v)
        assert all(v == 0.0 for v in m.values())


# ---------------------------------------------------------------------------
# compute_rigidity
# ---------------------------------------------------------------------------

class TestComputeRigidity:
    def test_returns_required_keys(self):
        axis_v, _ = ScreeningEngine.aggregate_vectors(TEST_RESPONSES)
        r = ScreeningEngine.compute_rigidity(TEST_RESPONSES, axis_v)
        assert {"polarization", "low_variance", "strategy_repetition", "total"} == set(r.keys())

    def test_all_values_in_0_1(self):
        axis_v, _ = ScreeningEngine.aggregate_vectors(TEST_RESPONSES)
        r = ScreeningEngine.compute_rigidity(TEST_RESPONSES, axis_v)
        for key, val in r.items():
            assert 0.0 <= val <= 1.0, f"{key}={val} out of [0,1]"

    def test_total_formula(self):
        axis_v, _ = ScreeningEngine.aggregate_vectors(TEST_RESPONSES)
        r = ScreeningEngine.compute_rigidity(TEST_RESPONSES, axis_v)
        expected = 0.3 * r["polarization"] + 0.3 * r["low_variance"] + 0.4 * r["strategy_repetition"]
        assert abs(r["total"] - min(1.0, expected)) < 1e-9

    def test_empty_responses(self):
        axis_v = {"A1": 0.0, "A2": 0.0, "A3": 0.0, "A4": 0.0}
        r = ScreeningEngine.compute_rigidity([], axis_v)
        assert r["total"] == 0.0

    def test_consistent_responses_high_repetition(self):
        """All identical responses → strategy_repetition == 1.0."""
        same = [{"axis_weights": {"A1": -0.5, "A2": 0.3, "A3": 0.1, "A4": -0.2},
                 "layer_weights": {"L0": 0.1, "L1": 0.0, "L2": 0.2, "L3": 0.0, "L4": 0.3}}] * 5
        axis_v, _ = ScreeningEngine.aggregate_vectors(same)
        r = ScreeningEngine.compute_rigidity(same, axis_v)
        assert r["strategy_repetition"] == 1.0


# ---------------------------------------------------------------------------
# compute_confidence
# ---------------------------------------------------------------------------

class TestComputeConfidence:
    def test_returns_float_in_range(self):
        axis_v, _ = ScreeningEngine.aggregate_vectors(TEST_RESPONSES)
        ambiguity_count = 3
        c = ScreeningEngine.compute_confidence(TEST_RESPONSES, axis_v, ambiguity_count)
        assert isinstance(c, float)
        assert 0.0 <= c <= 1.0

    def test_empty_responses_returns_zero(self):
        axis_v = {"A1": 0.0, "A2": 0.0, "A3": 0.0, "A4": 0.0}
        assert ScreeningEngine.compute_confidence([], axis_v, 0) == 0.0

    def test_high_ambiguity_lowers_confidence(self):
        axis_v, _ = ScreeningEngine.aggregate_vectors(TEST_RESPONSES)
        c_low_amb = ScreeningEngine.compute_confidence(TEST_RESPONSES, axis_v, 0)
        c_high_amb = ScreeningEngine.compute_confidence(TEST_RESPONSES, axis_v, 20)
        assert c_low_amb > c_high_amb

    def test_test_dataset_confidence_nonzero(self):
        axis_v, layer_v = ScreeningEngine.aggregate_vectors(TEST_RESPONSES)
        m = ScreeningEngine.compute_tension_matrix(axis_v, layer_v)
        zones = ScreeningEngine.find_ambiguity_zones(axis_v, layer_v, m)
        c = ScreeningEngine.compute_confidence(TEST_RESPONSES, axis_v, len(zones))
        assert c > 0.0


# ---------------------------------------------------------------------------
# find_ambiguity_zones
# ---------------------------------------------------------------------------

class TestFindAmbiguityZones:
    def test_returns_list(self):
        axis_v, layer_v = ScreeningEngine.aggregate_vectors(TEST_RESPONSES)
        m = ScreeningEngine.compute_tension_matrix(axis_v, layer_v)
        zones = ScreeningEngine.find_ambiguity_zones(axis_v, layer_v, m)
        assert isinstance(zones, list)

    def test_zone_format(self):
        axis_v, layer_v = ScreeningEngine.aggregate_vectors(TEST_RESPONSES)
        m = ScreeningEngine.compute_tension_matrix(axis_v, layer_v)
        zones = ScreeningEngine.find_ambiguity_zones(axis_v, layer_v, m)
        for zone in zones:
            apart, lpart = zone.split("_")
            assert apart.startswith("A"), f"Expected 'A..._L...' format, got {zone}"
            assert lpart.startswith("L"), f"Expected 'A..._L...' format, got {zone}"

    def test_weak_vectors_produce_many_zones(self):
        """Near-zero vectors → all 20 cells ambiguous."""
        axis_v = {a: 0.01 for a in AXES}
        layer_v = {l: 0.01 for l in LAYERS}
        m = ScreeningEngine.compute_tension_matrix(axis_v, layer_v)
        zones = ScreeningEngine.find_ambiguity_zones(axis_v, layer_v, m)
        assert len(zones) == 20

    def test_strong_vectors_produce_few_zones(self):
        """Large vectors → no or few ambiguous cells."""
        axis_v = {a: 0.9 for a in AXES}
        layer_v = {l: 0.9 for l in LAYERS}
        m = ScreeningEngine.compute_tension_matrix(axis_v, layer_v)
        zones = ScreeningEngine.find_ambiguity_zones(axis_v, layer_v, m)
        assert len(zones) == 0


# ---------------------------------------------------------------------------
# get_dominant_cells
# ---------------------------------------------------------------------------

class TestGetDominantCells:
    def test_returns_top_n(self):
        axis_v, layer_v = ScreeningEngine.aggregate_vectors(TEST_RESPONSES)
        m = ScreeningEngine.compute_tension_matrix(axis_v, layer_v)
        cells = ScreeningEngine.get_dominant_cells(m, top_n=3)
        assert len(cells) == 3

    def test_sorted_by_abs_descending(self):
        axis_v, layer_v = ScreeningEngine.aggregate_vectors(TEST_RESPONSES)
        m = ScreeningEngine.compute_tension_matrix(axis_v, layer_v)
        cells = ScreeningEngine.get_dominant_cells(m, top_n=5)
        values = [abs(m[c]) for c in cells]
        assert values == sorted(values, reverse=True)

    def test_custom_top_n(self):
        axis_v, layer_v = ScreeningEngine.aggregate_vectors(TEST_RESPONSES)
        m = ScreeningEngine.compute_tension_matrix(axis_v, layer_v)
        assert len(ScreeningEngine.get_dominant_cells(m, top_n=1)) == 1
        assert len(ScreeningEngine.get_dominant_cells(m, top_n=10)) == 10

    def test_top_cell_has_highest_abs_value(self):
        axis_v, layer_v = ScreeningEngine.aggregate_vectors(TEST_RESPONSES)
        m = ScreeningEngine.compute_tension_matrix(axis_v, layer_v)
        top_cell = ScreeningEngine.get_dominant_cells(m, top_n=1)[0]
        max_val = max(abs(v) for v in m.values())
        assert abs(m[top_cell]) == max_val


# ---------------------------------------------------------------------------
# process_response
# ---------------------------------------------------------------------------

class TestProcessResponse:
    def test_returns_all_keys(self):
        state = {}
        result = ScreeningEngine.process_response(state, TEST_RESPONSES[0])
        expected_keys = {
            "response_history", "axis_vector", "layer_vector",
            "tension_matrix", "ambiguity_zones", "rigidity",
            "confidence", "dominant_cells",
        }
        assert expected_keys == set(result.keys())

    def test_response_history_grows(self):
        state = {}
        for i, r in enumerate(TEST_RESPONSES[:5]):
            state = ScreeningEngine.process_response(state, r)
            assert len(state["response_history"]) == i + 1

    def test_does_not_mutate_current_state(self):
        state = {"response_history": [TEST_RESPONSES[0]]}
        original_len = len(state["response_history"])
        ScreeningEngine.process_response(state, TEST_RESPONSES[1])
        assert len(state["response_history"]) == original_len

    def test_full_pipeline_14_responses(self):
        """Process all 14 responses sequentially; final state must satisfy axis/layer invariants."""
        state: dict = {}
        for r in TEST_RESPONSES:
            state = ScreeningEngine.process_response(state, r)

        axis_v = state["axis_vector"]
        layer_v = state["layer_vector"]

        assert axis_v["A1"] < 0, f"A1={axis_v['A1']}"
        assert axis_v["A2"] < 0, f"A2={axis_v['A2']}"
        assert axis_v["A3"] > 0, f"A3={axis_v['A3']}"
        assert axis_v["A4"] < 0, f"A4={axis_v['A4']}"
        assert layer_v["L4"] > layer_v["L2"] > layer_v["L0"], (
            f"L4={layer_v['L4']:.3f} L2={layer_v['L2']:.3f} L0={layer_v['L0']:.3f}"
        )

        assert len(state["response_history"]) == 14
        assert len(state["tension_matrix"]) == 20
        assert isinstance(state["confidence"], float)
        assert 0.0 <= state["confidence"] <= 1.0
        assert len(state["dominant_cells"]) == 3
