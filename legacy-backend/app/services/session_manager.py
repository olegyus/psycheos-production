"""
Session Manager - handles screening session lifecycle.
"""

import json
import secrets
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.specialist import Specialist
from app.models.session import ScreeningSession, SessionStatus
from app.models.output import ScreeningOutput
from app.exceptions import (
    SpecialistNotFoundError,
    SessionNotFoundError,
    SessionExpiredError,
    SessionAlreadyCompletedError,
    SessionNotStartedError,
    InvalidSessionStateError,
    InsufficientTokensError,
)
from app.services.token_manager import TokenManager
from app.logging_config import get_logger

logger = get_logger(__name__)


class SessionManager:
    """Manages screening session lifecycle."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self.token_manager = TokenManager(db)
    
    async def create_session(
        self,
        specialist_telegram_id: int,
        client_identifier: str | None = None,
    ) -> ScreeningSession:
        """
        Create a new screening session.
        
        Args:
            specialist_telegram_id: Specialist's Telegram ID
            client_identifier: Optional client identifier
            
        Returns:
            Created ScreeningSession
            
        Raises:
            SpecialistNotFoundError: If specialist not found
            InsufficientTokensError: If not enough tokens
        """
        # Get specialist
        stmt = select(Specialist).where(Specialist.telegram_id == specialist_telegram_id)
        result = await self.db.execute(stmt)
        specialist = result.scalar_one_or_none()
        
        if specialist is None:
            raise SpecialistNotFoundError(specialist_telegram_id)
        
        # Generate unique session ID
        session_id = self._generate_session_id()
        
        # Create initial session state
        initial_state = self._create_initial_state(session_id)
        
        # Create session
        session = ScreeningSession(
            session_id=session_id,
            specialist_id=specialist.id,
            client_identifier=client_identifier,
            status=SessionStatus.CREATED.value,
            session_state=json.dumps(initial_state, ensure_ascii=False),
            screens_completed=0,
            created_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(hours=settings.session_expiry_hours),
        )
        
        self.db.add(session)
        await self.db.flush()

        # Spend token
        await self.token_manager.spend_token(
            telegram_id=specialist_telegram_id,
            session_id=session_id,
            description="Token spent for new screening session"
        )
        
        
        logger.info(
            "session_created",
            session_id=session_id,
            specialist_id=specialist.id,
            expires_at=session.expires_at.isoformat(),
        )
        
        return session
    
    async def start_session(
        self,
        session_id: str,
        client_telegram_id: int | None = None,
    ) -> ScreeningSession:
        """
        Start a session (when client opens the link).
        
        Args:
            session_id: Session ID
            client_telegram_id: Client's Telegram ID
            
        Returns:
            Updated ScreeningSession
        """
        session = await self.get_session(session_id)
        
        # Check if expired
        if session.is_expired:
            session.status = SessionStatus.EXPIRED.value
            await self.db.flush()
            raise SessionExpiredError(session_id)
        
        # Check if already completed
        if session.is_completed:
            raise SessionAlreadyCompletedError(session_id)
        
        # Update session
        session.status = SessionStatus.IN_PROGRESS.value
        session.client_telegram_id = client_telegram_id
        session.started_at = datetime.utcnow()
        
        await self.db.flush()
        
        logger.info(
            "session_started",
            session_id=session_id,
            client_telegram_id=client_telegram_id,
        )
        
        return session
    
    async def get_session(self, session_id: str) -> ScreeningSession:
        """
        Get session by ID.
        
        Raises:
            SessionNotFoundError: If session not found
        """
        stmt = (
            select(ScreeningSession)
            .where(ScreeningSession.session_id == session_id)
            .options(selectinload(ScreeningSession.output))
        )
        result = await self.db.execute(stmt)
        session = result.scalar_one_or_none()
        
        if session is None:
            raise SessionNotFoundError(session_id)
        
        return session
    
    async def get_session_state(self, session_id: str) -> dict:
        """Get parsed session state."""
        session = await self.get_session(session_id)
        
        if session.session_state:
            return json.loads(session.session_state)
        return {}
    
    async def update_session_state(
        self,
        session_id: str,
        screen_id: str,
        response_value: int | str,
        screen_data: dict | None = None,
    ) -> ScreeningSession:
        """
        Update session state with a new response.
        
        Args:
            session_id: Session ID
            screen_id: Screen that was answered
            response_value: Client's response
            screen_data: Screen metadata (continuum, context, screen_type)
            
        Returns:
            Updated ScreeningSession
        """
        session = await self.get_session(session_id)
        
        # Validate session state
        if session.status != SessionStatus.IN_PROGRESS.value:
            if session.status == SessionStatus.CREATED.value:
                raise SessionNotStartedError(session_id)
            elif session.status == SessionStatus.COMPLETED.value:
                raise SessionAlreadyCompletedError(session_id)
            else:
                raise InvalidSessionStateError(
                    session_id, session.status, SessionStatus.IN_PROGRESS.value
                )
        
        # Check expiration
        if session.is_expired:
            session.status = SessionStatus.EXPIRED.value
            await self.db.flush()
            raise SessionExpiredError(session_id)
        
        # Parse current state
        state = json.loads(session.session_state) if session.session_state else {}
        
        # Extract screen metadata
        continuum = screen_data.get("continuum") if screen_data else None
        context = screen_data.get("context") if screen_data else None
        screen_type = screen_data.get("screen_type", "slider") if screen_data else "slider"
        
        # Add to response_history
        response_entry = {
            "screen_id": screen_id,
            "response_value": response_value,
            "response_time": 0,  # TODO: track actual response time
            "timestamp": datetime.utcnow().isoformat(),
            "continuum": continuum,
            "context": context,
        }
        state["response_history"].append(response_entry)
        
        # Update screens_shown
        state["screens_shown"] = len(state["response_history"])
        
        # Update screens_by_type
        if screen_type in state["progress"]["screens_by_type"]:
            state["progress"]["screens_by_type"][screen_type] += 1
        
        # Update contexts_covered
        if context and context not in state["progress"]["contexts_covered"]:
            state["progress"]["contexts_covered"].append(context)
        
        # Update continuum_scores if we have numeric response and continuum
        # For context block (B0), use "context" as continuum
        effective_continuum = continuum
        if not effective_continuum and screen_data:
            block = screen_data.get("block")
            if block == "context":
                effective_continuum = "context"
        
        if effective_continuum and isinstance(response_value, (int, float)):
            continuum_data = state["continuum_scores"].get(effective_continuum)
            if continuum_data:
                # Add response
                continuum_data["responses"].append(response_value)
                           
                # Recalculate position (mean)
                responses = continuum_data["responses"]
                continuum_data["position"] = sum(responses) / len(responses)
                
                # Recalculate variability (std dev)
                if len(responses) > 1:
                    mean = continuum_data["position"]
                    variance = sum((x - mean) ** 2 for x in responses) / len(responses)
                    continuum_data["variability"] = variance ** 0.5
                
                # Update context_map
                if context:
                    continuum_data["context_map"][context] = response_value
                
                # Update confidence (0.2 per response, max 1.0)
                continuum_data["confidence"] = min(1.0, len(responses) * 0.2)
                
                # Mark continuum as covered if confidence >= 0.4
                if continuum_data["confidence"] >= 0.4:
                    state["progress"]["covered_continua"][effective_continuum] = True
        
        # Update flags
        self._update_flags(state)
        
        # Update completion_level
        covered_count = sum(1 for v in state["progress"]["covered_continua"].values() if v)
        contexts_count = len(state["progress"]["contexts_covered"])
        state["progress"]["completion_level"] = (covered_count / 4 * 0.5) + (contexts_count / 5 * 0.5)
        
        # Update session
        session.session_state = json.dumps(state, ensure_ascii=False)
        session.screens_completed = state["screens_shown"]
        
        await self.db.flush()
        
        logger.info(
            "session_state_updated",
            session_id=session_id,
            screen_id=screen_id,
            screens_completed=session.screens_completed,
            completion_level=state["progress"]["completion_level"],
        )
        
        return session
    
    def _update_flags(self, state: dict) -> None:
        """Update behavioral flags based on current state."""
        flags = state["flags"]
        
        # Check rigidity (any continuum with variability < 10)
        flags["rigidity"] = any(
            cs["variability"] is not None and cs["variability"] < 10
            for cs in state["continuum_scores"].values()
            if len(cs["responses"]) >= 3
        )
        
        # Check high_variability (any continuum with variability > 30)
        flags["high_variability"] = any(
            cs["variability"] is not None and cs["variability"] > 30
            for cs in state["continuum_scores"].values()
            if len(cs["responses"]) >= 3
        )
        
        # Check contradiction (range > 50 in any continuum)
        flags["contradiction"] = any(
            len(cs["responses"]) >= 2 and (max(cs["responses"]) - min(cs["responses"])) > 50
            for cs in state["continuum_scores"].values()
        )
        
        # Check avoidance (>50% "зависит от" answers)
        # This requires checking for specific response patterns
        response_history = state.get("response_history", [])
        if response_history:
            avoidance_responses = sum(
                1 for r in response_history
                if isinstance(r.get("response_value"), str) and "зависит" in r["response_value"].lower()
            )
            flags["avoidance"] = (avoidance_responses / len(response_history)) > 0.5
        
        # Check overload (response_time > 60s on 3+ screens)
        # TODO: implement when we track response_time
        flags["overload"] = False
    
    async def finalize_session(
        self,
        session_id: str,
        screening_output: dict,
        interview_protocol: dict,
    ) -> ScreeningSession:
        """
        Finalize session and save output.
        
        Args:
            session_id: Session ID
            screening_output: Generated screening output
            interview_protocol: Generated interview protocol
            
        Returns:
            Completed ScreeningSession
        """
        session = await self.get_session(session_id)
        
        # Validate session state
        if session.status != SessionStatus.IN_PROGRESS.value:
            raise InvalidSessionStateError(
                session_id, session.status, SessionStatus.IN_PROGRESS.value
            )
        
        # Calculate duration
        duration_minutes = None
        if session.started_at:
            delta = datetime.utcnow() - session.started_at
            duration_minutes = int(delta.total_seconds() / 60)
        
        # Update session
        session.status = SessionStatus.COMPLETED.value
        session.completed_at = datetime.utcnow()
        session.duration_minutes = duration_minutes
        
        # Create output record
        output = ScreeningOutput(
            session_id=session_id,
            screening_output=json.dumps(screening_output, ensure_ascii=False),
            interview_protocol=json.dumps(interview_protocol, ensure_ascii=False),
            created_at=datetime.utcnow(),
        )
        
        self.db.add(output)
        await self.db.flush()
        
        logger.info(
            "session_finalized",
            session_id=session_id,
            duration_minutes=duration_minutes,
            screens_completed=session.screens_completed,
        )
        
        return session
    
    async def get_output(self, session_id: str) -> ScreeningOutput:
        """
        Get screening output for a session.
        
        Raises:
            SessionNotFoundError: If session/output not found
        """
        session = await self.get_session(session_id)
        
        if session.output is None:
            raise SessionNotFoundError(f"Output not found for session: {session_id}")
        
        # Mark as viewed
        session.output.mark_as_viewed()
        await self.db.flush()
        
        return session.output
    
    def _generate_session_id(self) -> str:
        """Generate unique session ID."""
        return secrets.token_urlsafe(16)
    
    def _create_initial_state(self, session_id: str) -> dict:
        """Create initial session state according to session_state_schema."""
        return {
            "session_id": session_id,
            "client_id": None,
            "start_time": datetime.utcnow().isoformat(),
            "screens_shown": 0,
            "progress": {
                "covered_continua": {
                    "context": False,
                    "economy_exploration": False,
                    "protection_contact": False,
                    "retention_movement": False,
                    "survival_development": False,
                },
                "screens_by_type": {
                    "info": 0,
                    "slider": 0,
                    "single_choice": 0,
                    "multi_choice": 0,
                },
                "contexts_covered": [],
                "completion_level": 0.0,
            },
            "continuum_scores": {
                "context": {
                    "position": None,
                    "responses": [],
                    "variability": None,
                    "context_map": {},
                    "confidence": 0.0,
                },
                "economy_exploration": {
                    "position": None,
                    "responses": [],
                    "variability": None,
                    "context_map": {},
                    "confidence": 0.0,
                },
                "protection_contact": {
                    "position": None,
                    "responses": [],
                    "variability": None,
                    "context_map": {},
                    "confidence": 0.0,
                },
                "retention_movement": {
                    "position": None,
                    "responses": [],
                    "variability": None,
                    "context_map": {},
                    "confidence": 0.0,
                },
                "survival_development": {
                    "position": None,
                    "responses": [],
                    "variability": None,
                    "context_map": {},
                    "confidence": 0.0,
                },
            },
            "flags": {
                "rigidity": False,
                "high_variability": False,
                "contradiction": False,
                "avoidance": False,
                "overload": False,
            },
            "response_history": [],
        }