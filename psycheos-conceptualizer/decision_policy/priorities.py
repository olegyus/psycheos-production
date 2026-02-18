"""Priority system for Decision Policy."""

from typing import Tuple
from enum import IntEnum
from core.models import SessionState
from core.enums import HypothesisType, ConfidenceLevel


class Priority(IntEnum):
    """Priority levels for question selection."""
    CRITICAL = 1  # No managerial hypothesis
    HIGH = 2      # Dominant without alternatives
    MEDIUM = 3    # Structural issues
    LOW = 4       # Refinement
    NONE = 5      # No specific priority


class PriorityChecker:
    """Checks session state and determines priority level."""
    
    def __init__(self, session: SessionState):
        self.session = session
        self.active_hypotheses = session.get_active_hypotheses()
        self.managerial_hypotheses = session.get_managerial_hypotheses()
    
    def check_priority(self) -> Tuple[Priority, str]:
        """Check session state and return highest priority need."""
        
        # PRIORITY 1: No managerial hypothesis
        priority, reason = self._check_no_managerial()
        if priority == Priority.CRITICAL:
            return priority, reason
        
        # PRIORITY 2: Dominant without alternatives
        priority, reason = self._check_dominant_without_alternatives()
        if priority == Priority.HIGH:
            return priority, reason
        
        # PRIORITY 3: Structural issues
        priority, reason = self._check_structural_issues()
        if priority == Priority.MEDIUM:
            return priority, reason
        
        # PRIORITY 4: Refinement
        priority, reason = self._check_refinement_needed()
        if priority == Priority.LOW:
            return priority, reason
        
        return Priority.NONE, "No specific priority - general exploration"
    
    def _check_no_managerial(self) -> Tuple[Priority, str]:
        """Priority 1: Check for absence of managerial hypothesis."""
        structural_count = sum(1 for h in self.active_hypotheses if h.type == HypothesisType.STRUCTURAL)
        functional_count = sum(1 for h in self.active_hypotheses if h.type == HypothesisType.FUNCTIONAL)
        dynamic_count = sum(1 for h in self.active_hypotheses if h.type == HypothesisType.DYNAMIC)
        managerial_count = len(self.managerial_hypotheses)
        
        has_understanding = (structural_count > 0 or functional_count > 0 or dynamic_count > 0)
        
        if has_understanding and managerial_count == 0:
            return (
                Priority.CRITICAL,
                f"Have understanding ({structural_count}S+{functional_count}F+{dynamic_count}D) but NO managerial hypothesis. Need leverage point."
            )
        
        if len(self.active_hypotheses) >= 3 and managerial_count == 0:
            return (
                Priority.CRITICAL,
                f"Model has {len(self.active_hypotheses)} hypotheses but no management point."
            )
        
        return Priority.NONE, ""
    
    def _check_dominant_without_alternatives(self) -> Tuple[Priority, str]:
        """Priority 2: Check for dominant hypothesis without alternatives."""
        dominant = [h for h in self.active_hypotheses if h.confidence == ConfidenceLevel.DOMINANT]
        
        if len(dominant) == 0:
            return Priority.NONE, ""
        
        for dom_hyp in dominant:
            same_type = [h for h in self.active_hypotheses if h.type == dom_hyp.type and h.id != dom_hyp.id]
            
            if len(same_type) == 0:
                return (
                    Priority.HIGH,
                    f"Dominant {dom_hyp.type.value} hypothesis has no alternatives. Need to test against competing explanations."
                )
        
        return Priority.NONE, ""
    
    def _check_structural_issues(self) -> Tuple[Priority, str]:
        """Priority 3: Check for structural issues."""
        if len(self.active_hypotheses) == 0:
            return Priority.NONE, ""
        
        # Many hypotheses without structure
        if len(self.active_hypotheses) >= 5:
            structural = [h for h in self.active_hypotheses if h.type == HypothesisType.STRUCTURAL]
            
            if len(structural) == 0:
                return (
                    Priority.MEDIUM,
                    f"Have {len(self.active_hypotheses)} hypotheses but no structural hypothesis. Need leading configuration."
                )
            
            confident = [h for h in self.active_hypotheses 
                        if h.confidence in [ConfidenceLevel.WORKING, ConfidenceLevel.DOMINANT]]
            
            if len(confident) == 0:
                return (
                    Priority.MEDIUM,
                    f"Have {len(self.active_hypotheses)} hypotheses but all weak/conditional. Need to strengthen."
                )
        
        # All hypotheses on one layer
        if len(self.active_hypotheses) >= 3:
            all_layers = set()
            for hyp in self.active_hypotheses:
                all_layers.update(hyp.levels)
            
            if len(all_layers) == 1:
                layer = list(all_layers)[0]
                return (
                    Priority.MEDIUM,
                    f"All hypotheses on {layer.value}. Need multi-layer understanding."
                )
        
        return Priority.NONE, ""
    
    def _check_refinement_needed(self) -> Tuple[Priority, str]:
        """Priority 4: Check if model nearly complete."""
        managerial_count = len(self.managerial_hypotheses)
        total_count = len(self.active_hypotheses)
        
        if managerial_count == 0:
            return Priority.NONE, ""
        
        if total_count < 2 or total_count > 6:
            return Priority.NONE, ""
        
        types_present = set(h.type for h in self.active_hypotheses)
        
        if len(types_present) >= 2 and managerial_count >= 1:
            return (
                Priority.LOW,
                f"Model nearly complete: {total_count} hypotheses including {managerial_count} managerial. Can refine dynamics."
            )
        
        return Priority.NONE, ""


def check_session_priority(session: SessionState) -> Tuple[Priority, str]:
    """Convenience function to check priority for a session."""
    checker = PriorityChecker(session)
    return checker.check_priority()


__all__ = [
    "Priority",
    "PriorityChecker",
    "check_session_priority",
]
