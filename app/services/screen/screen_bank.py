"""Screen v2 bank access helpers.

Provides lookup functions over PHASE1_SCREENS and PHASE2_TEMPLATES
defined in weight_matrix.py.
"""

from app.services.screen.weight_matrix import (
    PHASE1_SCREENS,
    PHASE2_TEMPLATES,
    _PHASE2_INDEX,
)


def get_phase1_screen(index: int) -> dict:
    """Return Phase 1 screen by zero-based index (0–5).

    Raises IndexError for out-of-range indices.
    """
    if not (0 <= index < len(PHASE1_SCREENS)):
        raise IndexError(
            f"Phase 1 screen index {index} out of range "
            f"(valid: 0–{len(PHASE1_SCREENS) - 1})"
        )
    return PHASE1_SCREENS[index]


def get_phase2_template(node: str) -> dict:
    """Return Phase 2 diagnostic template by node key (e.g. 'A1_L0').

    Raises KeyError if the node is not found.
    """
    if node not in _PHASE2_INDEX:
        valid = ", ".join(sorted(_PHASE2_INDEX))
        raise KeyError(f"Phase 2 node '{node}' not found. Valid nodes: {valid}")
    return _PHASE2_INDEX[node]


def get_all_phase2_nodes() -> list[str]:
    """Return all 20 Phase 2 node keys in definition order."""
    return [t["node"] for t in PHASE2_TEMPLATES]
