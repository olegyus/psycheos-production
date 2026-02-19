"""Screen v2 phase orchestrator.

Drives the 3-phase screening flow:
  Phase 1 — 6 fixed multi-select screens (rule-based)
  Phase 2 — up to 3 Claude-routed adaptive questions
  Phase 3 — up to 5 Claude-constructed questions (deeper)
  Report  — final structural report generated via Claude

All DB work uses the injected AsyncSession; the caller must commit.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import UUID

import anthropic
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.screening_assessment import ScreeningAssessment
from app.services.screen import screen_bank
from app.services.screen.engine import ScreeningEngine
from app.services.screen.prompts import (
    PHASE2_ROUTER_PROMPT,
    PHASE2_STOP_PROMPT,
    PHASE3_CONSTRUCTOR_PROMPT,
    assemble_prompt,
)

logger = logging.getLogger(__name__)

_HAIKU = "claude-haiku-4-5-20251001"
_SONNET = "claude-sonnet-4-5-20250929"
_CONFIDENCE_THRESHOLD = 0.85
_MAX_PHASE2_QUESTIONS = 3
_MAX_PHASE3_QUESTIONS = 5


class ScreenOrchestrator:
    """Stateless phase orchestrator — one instance per request is fine."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.engine = ScreeningEngine()
        self.client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_or_create_session_state(self, assessment_id: UUID) -> dict:
        """Load current assessment state from DB as a plain dict.

        Raises ValueError if the assessment does not exist.
        """
        result = await self.db.execute(
            select(ScreeningAssessment).where(ScreeningAssessment.id == assessment_id)
        )
        assessment = result.scalar_one_or_none()
        if assessment is None:
            raise ValueError(f"ScreeningAssessment {assessment_id} not found")

        return {
            "assessment_id": str(assessment.id),
            "context_id": str(assessment.context_id),
            "status": assessment.status,
            "phase": assessment.phase,
            "phase1_completed": assessment.phase1_completed,
            "phase2_questions": assessment.phase2_questions,
            "phase3_questions": assessment.phase3_questions,
            # Engine state
            "response_history": assessment.response_history or [],
            "axis_vector": assessment.axis_vector or {},
            "layer_vector": assessment.layer_vector or {},
            "tension_matrix": assessment.tension_matrix or {},
            "rigidity": assessment.rigidity or {},
            "confidence": assessment.confidence or 0.0,
            "ambiguity_zones": assessment.ambiguity_zones or [],
            "dominant_cells": assessment.dominant_cells or [],
        }

    async def start_assessment(self, assessment_id: UUID) -> dict:
        """Mark assessment as started and return the first Phase 1 screen."""
        await self.db.execute(
            update(ScreeningAssessment)
            .where(ScreeningAssessment.id == assessment_id)
            .values(
                phase=1,
                status="in_progress",
                started_at=datetime.now(timezone.utc),
            )
        )
        await self.db.flush()

        first_screen = screen_bank.get_phase1_screen(0)
        return {
            "action": "show_screen",
            "screen": first_screen,
            "screen_index": 0,
            "phase": 1,
        }

    async def process_phase1_response(
        self,
        assessment_id: UUID,
        screen_index: int,
        selected_options: list[int],
    ) -> dict:
        """Process a Phase 1 multi-select answer and advance the session."""
        state = await self.get_or_create_session_state(assessment_id)
        screen = screen_bank.get_phase1_screen(screen_index)

        # Each selected option becomes an independent response for the engine
        for idx in selected_options:
            option = screen["options"][idx]
            response = {
                "axis_weights": option["axis_weights"],
                "layer_weights": option["layer_weights"],
            }
            state = self.engine.process_response(state, response)

        await self._save_engine_state(assessment_id, state)

        if screen_index < 5:
            next_screen = screen_bank.get_phase1_screen(screen_index + 1)
            return {
                "action": "show_screen",
                "screen": next_screen,
                "screen_index": screen_index + 1,
                "phase": 1,
            }

        # Last Phase 1 screen — decide what comes next
        await self.db.execute(
            update(ScreeningAssessment)
            .where(ScreeningAssessment.id == assessment_id)
            .values(phase1_completed=True)
        )
        await self.db.flush()

        if state["confidence"] >= _CONFIDENCE_THRESHOLD:
            report = await self._generate_report(assessment_id)
            return {"action": "complete", "report": report}

        await self.db.execute(
            update(ScreeningAssessment)
            .where(ScreeningAssessment.id == assessment_id)
            .values(phase=2)
        )
        await self.db.flush()

        question_screen = await self._select_next_phase2_question(state)
        return {"action": "show_screen", "screen": question_screen, "phase": 2}

    async def process_phase2_response(
        self,
        assessment_id: UUID,
        selected_options: list[int],
        current_screen: dict,
    ) -> dict:
        """Process one Phase 2 adaptive response."""
        state = await self.get_or_create_session_state(assessment_id)
        prev_axis_vector = dict(state.get("axis_vector", {}))

        for idx in selected_options:
            option = current_screen["options"][idx]
            response = {
                "axis_weights": option["axis_weights"],
                "layer_weights": option["layer_weights"],
            }
            state = self.engine.process_response(state, response)

        new_q_count = state["phase2_questions"] + 1
        state["phase2_questions"] = new_q_count
        await self._save_engine_state(assessment_id, state, phase2_questions=new_q_count)

        stop = await self._check_stop_phase2(state, prev_axis_vector)

        if stop or new_q_count >= _MAX_PHASE2_QUESTIONS and state["confidence"] >= _CONFIDENCE_THRESHOLD:
            report = await self._generate_report(assessment_id)
            return {"action": "complete", "report": report}

        if new_q_count < _MAX_PHASE2_QUESTIONS:
            q = await self._select_next_phase2_question(state)
            return {"action": "show_screen", "screen": q, "phase": 2}

        # Reached max Phase 2 questions
        if state["confidence"] < _CONFIDENCE_THRESHOLD:
            await self.db.execute(
                update(ScreeningAssessment)
                .where(ScreeningAssessment.id == assessment_id)
                .values(phase=3)
            )
            await self.db.flush()
            q = await self._select_next_phase3_question(state)
            return {"action": "show_screen", "screen": q, "phase": 3}

        report = await self._generate_report(assessment_id)
        return {"action": "complete", "report": report}

    async def process_phase3_response(
        self,
        assessment_id: UUID,
        selected_options: list[int],
        current_screen: dict,
    ) -> dict:
        """Process one Phase 3 constructor response."""
        state = await self.get_or_create_session_state(assessment_id)

        for idx in selected_options:
            option = current_screen["options"][idx]
            response = {
                "axis_weights": option["axis_weights"],
                "layer_weights": option["layer_weights"],
            }
            state = self.engine.process_response(state, response)

        new_q_count = state["phase3_questions"] + 1
        state["phase3_questions"] = new_q_count
        await self._save_engine_state(assessment_id, state, phase3_questions=new_q_count)

        if new_q_count >= _MAX_PHASE3_QUESTIONS or state["confidence"] >= _CONFIDENCE_THRESHOLD:
            report = await self._generate_report(assessment_id)
            return {"action": "complete", "report": report}

        q = await self._select_next_phase3_question(state)
        return {"action": "show_screen", "screen": q, "phase": 3}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _select_next_phase2_question(self, state: dict) -> dict:
        """Use Claude (haiku) to pick the best ambiguity node to explore."""
        context = {
            "AxisVector": state.get("axis_vector", {}),
            "LayerVector": state.get("layer_vector", {}),
            "RigidityIndex": state.get("rigidity", {}),
            "AmbiguityZones": state.get("ambiguity_zones", []),
            "Confidence": state.get("confidence", 0.0),
        }
        user_content = assemble_prompt("router", context)

        raw = await self._call_claude(
            system="You are the routing module of PsycheOS Screening v2. "
                   "Respond only with valid JSON.",
            user_content=user_content,
            model=_HAIKU,
        )

        selected_node = self._fallback_node(state)
        if raw:
            try:
                data = _parse_json(raw)
                selected_node = data["selected_node"]
            except Exception:
                logger.warning("[screen] Router JSON parse failed; using fallback node")

        try:
            template = screen_bank.get_phase2_template(selected_node)
        except KeyError:
            template = screen_bank.get_phase2_template(
                screen_bank.get_all_phase2_nodes()[0]
            )

        return {
            "question": template["reference_question"],
            "options": template["options"],
            "node": template["node"],
            "diagnostic_split": template["diagnostic_split"],
            "type": "multi_select",
        }

    async def _select_next_phase3_question(self, state: dict) -> dict:
        """Use Claude (sonnet) to construct a deeper adaptive question."""
        # Pick the top ambiguity node as the diagnostic target
        selected_node = self._fallback_node(state)
        try:
            template = screen_bank.get_phase2_template(selected_node)
        except KeyError:
            template = screen_bank.get_phase2_template(
                screen_bank.get_all_phase2_nodes()[0]
            )

        context = {
            "DiagnosticNode": selected_node,
            "DiagnosticSplit": template["diagnostic_split"],
            "ReferenceTemplate": {
                "question": template["reference_question"],
                "options": [
                    {"text": o["text"], "split": o.get("split")}
                    for o in template["options"]
                ],
            },
            "AxisVector": state.get("axis_vector", {}),
            "LayerVector": state.get("layer_vector", {}),
        }
        user_content = assemble_prompt("constructor", context)

        raw = await self._call_claude(
            system="You are the adaptive question constructor of PsycheOS Screening v2. "
                   "Respond only with valid JSON. Question and options must be in Russian.",
            user_content=user_content,
            model=_SONNET,
            max_tokens=1500,
        )

        if raw:
            try:
                data = _parse_json(raw)
                return {
                    "question": data["question"],
                    "options": data["options"],
                    "node": selected_node,
                    "diagnostic_goal": data.get("diagnostic_goal", ""),
                    "type": "multi_select",
                }
            except Exception:
                logger.warning("[screen] Constructor JSON parse failed; using reference template")

        # Fallback: use the reference template unchanged
        return {
            "question": template["reference_question"],
            "options": template["options"],
            "node": selected_node,
            "diagnostic_split": template["diagnostic_split"],
            "type": "multi_select",
        }

    async def _check_stop_phase2(
        self, state: dict, prev_axis_vector: dict
    ) -> bool:
        """Ask Claude (haiku) whether Phase 2 should stop."""
        updated = state.get("axis_vector", {})
        delta = {
            a: abs(updated.get(a, 0.0) - prev_axis_vector.get(a, 0.0))
            for a in ["A1", "A2", "A3", "A4"]
        }
        conflict_index = sum(delta.values()) / len(delta)

        context = {
            "PreviousAxisVector": prev_axis_vector,
            "UpdatedAxisVector": updated,
            "ConflictIndex": round(conflict_index, 4),
            "Confidence": state.get("confidence", 0.0),
            "QuestionsAsked": state.get("phase2_questions", 0),
        }
        user_content = assemble_prompt("stop", context)

        raw = await self._call_claude(
            system="You are the phase-control module of PsycheOS Screening v2. "
                   "Respond only with valid JSON.",
            user_content=user_content,
            model=_HAIKU,
        )

        if raw:
            try:
                data = _parse_json(raw)
                return bool(data.get("stop_phase2", False))
            except Exception:
                logger.warning("[screen] Stop-decision JSON parse failed; using local fallback")

        # Local fallback
        questions_asked = state.get("phase2_questions", 0)
        confidence = state.get("confidence", 0.0)
        all_delta_small = all(v < 0.1 for v in delta.values())
        return all_delta_small or confidence >= _CONFIDENCE_THRESHOLD or questions_asked >= _MAX_PHASE2_QUESTIONS

    async def _generate_report(self, assessment_id: UUID) -> dict:
        """Generate and persist the final report."""
        from app.services.screen import report as report_module

        state = await self.get_or_create_session_state(assessment_id)
        result = await report_module.generate_full_report(state, self.client)

        await self.db.execute(
            update(ScreeningAssessment)
            .where(ScreeningAssessment.id == assessment_id)
            .values(
                report_json=result["report_json"],
                report_text=result["report_text"],
                status="completed",
                completed_at=datetime.now(timezone.utc),
            )
        )
        await self.db.flush()
        return result

    async def _call_claude(
        self,
        system: str,
        user_content: str,
        model: str = _HAIKU,
        max_tokens: int = 1000,
    ) -> str | None:
        """Thin wrapper over the Anthropic async client.

        Returns the text of the first content block, or None on any error.
        """
        try:
            resp = await self.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_content}],
                temperature=0.3,
                top_p=0.8,
            )
            return resp.content[0].text
        except Exception:
            logger.exception("[screen] Claude API call failed (model=%s)", model)
            return None

    async def _save_engine_state(
        self,
        assessment_id: UUID,
        state: dict,
        *,
        phase2_questions: int | None = None,
        phase3_questions: int | None = None,
    ) -> None:
        """Persist engine vector state + optional counter overrides."""
        values: dict = {
            "axis_vector": state.get("axis_vector", {}),
            "layer_vector": state.get("layer_vector", {}),
            "tension_matrix": state.get("tension_matrix", {}),
            "rigidity": state.get("rigidity", {}),
            "confidence": state.get("confidence", 0.0),
            "ambiguity_zones": state.get("ambiguity_zones", []),
            "dominant_cells": state.get("dominant_cells", []),
            "response_history": state.get("response_history", []),
        }
        if phase2_questions is not None:
            values["phase2_questions"] = phase2_questions
        if phase3_questions is not None:
            values["phase3_questions"] = phase3_questions

        await self.db.execute(
            update(ScreeningAssessment)
            .where(ScreeningAssessment.id == assessment_id)
            .values(**values)
        )
        await self.db.flush()

    def _fallback_node(self, state: dict) -> str:
        """Return the first ambiguity zone node, or first available Phase 2 node."""
        zones = state.get("ambiguity_zones", [])
        if zones:
            # zones format: "A{j}_L{k}" — matches Phase 2 template keys
            node = zones[0]
            all_nodes = screen_bank.get_all_phase2_nodes()
            if node in all_nodes:
                return node
        return screen_bank.get_all_phase2_nodes()[0]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_json(text: str) -> dict:
    """Strip optional markdown fences then parse JSON."""
    t = text.strip()
    if t.startswith("```json"):
        t = t[7:]
    if t.startswith("```"):
        t = t[3:]
    if t.endswith("```"):
        t = t[:-3]
    return json.loads(t.strip())
