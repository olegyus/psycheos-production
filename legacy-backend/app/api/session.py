"""
Session API endpoints.
"""

import json
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.schemas import (
    SessionCreateRequest,
    SessionCreateResponse,
    SessionStartResponse,
    SessionStatusResponse,
    NextScreenResponse,
    ScreenResponse,
    ClientResponseRequest,
    ClientResponseResponse,
    FinalizeResponse,
    OutputResponse,
    ErrorResponse,
)
from app.services.session_manager import SessionManager
from app.services.claude_orchestrator import claude_orchestrator
from app.services.screen_bank_loader import screen_bank_loader
from app.exceptions import (
    SpecialistNotFoundError,
    SessionNotFoundError,
    SessionExpiredError,
    SessionAlreadyCompletedError,
    SessionNotStartedError,
    InvalidSessionStateError,
    InsufficientTokensError,
    ScreenNotFoundError,
)
from app.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/session", tags=["session"])


def _format_screen_response(screen: dict) -> ScreenResponse:
    """Format screen data for API response."""
    return ScreenResponse(
        screen_id=screen.get("screen_id", ""),
        screen_type=screen.get("screen_type", "slider"),
        stimulus=screen.get("stimulus", {"situation": "", "question": ""}),
        response_format=screen.get("response_format", {}),
        continuum=screen.get("continuum"),
        context=screen.get("context"),
        load_level=screen.get("load_level"),
    )


@router.post(
    "/create",
    response_model=SessionCreateResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"model": ErrorResponse, "description": "Specialist not found"},
        402: {"model": ErrorResponse, "description": "Insufficient tokens"},
    }
)
async def create_session(
    data: SessionCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new screening session.
    
    Spends 1 token from specialist's balance.
    Returns session_id and deep link for client.
    """
    logger.info(
        "create_session_request",
        specialist_telegram_id=data.specialist_telegram_id,
        client_identifier=data.client_identifier,
    )
    
    session_manager = SessionManager(db)
    
    try:
        session = await session_manager.create_session(
            specialist_telegram_id=data.specialist_telegram_id,
            client_identifier=data.client_identifier,
        )
        
        # Generate deep link (will be configured in settings)
        deep_link = f"https://t.me/PsycheOS_Client_Test_bot?start={session.session_id}"
        
        return SessionCreateResponse(
            session_id=session.session_id,
            deep_link=deep_link,
            expires_at=session.expires_at,
            tokens_spent=1,
        )
    
    except SpecialistNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Specialist not found"
        )
    except InsufficientTokensError as e:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Insufficient tokens: {e.details['available']} available, 1 required"
        )


@router.post(
    "/{session_id}/start",
    response_model=SessionStartResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Session not found"},
        410: {"model": ErrorResponse, "description": "Session expired"},
        409: {"model": ErrorResponse, "description": "Session already completed"},
    }
)
async def start_session(
    session_id: str,
    client_telegram_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Start a session (when client opens the deep link).
    
    Returns the first screen to show.
    """
    logger.info(
        "start_session_request",
        session_id=session_id,
        client_telegram_id=client_telegram_id,
    )
    
    session_manager = SessionManager(db)
    
    try:
        session = await session_manager.start_session(
            session_id=session_id,
            client_telegram_id=client_telegram_id,
        )
        
        # Get first screen
        first_screen = claude_orchestrator.get_first_screen()
        first_screen_response = None
        if first_screen:
            first_screen_response = _format_screen_response(first_screen).__dict__
        logger.info(f"First screen: {first_screen} \n First screen response: {first_screen_response}")
        return SessionStartResponse(
            session_id=session.session_id,
            status=session.status,
            first_screen=first_screen_response,
        )
    
    except SessionNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )
    except SessionExpiredError:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Session has expired"
        )
    except SessionAlreadyCompletedError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Session already completed"
        )


@router.get(
    "/{session_id}/next_screen",
    response_model=NextScreenResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Session not found"},
        410: {"model": ErrorResponse, "description": "Session expired"},
        400: {"model": ErrorResponse, "description": "Invalid session state"},
    }
)
async def get_next_screen(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Get the next screen to show to the client.
    
    Uses Claude API for routing decisions.
    """
    logger.info("get_next_screen_request", session_id=session_id)
    
    session_manager = SessionManager(db)
    
    try:
        session = await session_manager.get_session(session_id)
        
        # Check session state
        if session.is_expired:
            raise SessionExpiredError(session_id)
        
        if session.is_completed:
            raise SessionAlreadyCompletedError(session_id)
        
        # Get session state
        state = await session_manager.get_session_state(session_id)
        
                # For first 4 screens, use deterministic initial sequence (no Claude needed)
        screens_shown = state.get("screens_shown", 0)
        
        if screens_shown < 25:
            # All 25 screens in fixed order (5 blocks × 5 screens)
            full_sequence = [
                # Block 0: Context
                "B0_01", "B0_02", "B0_03", "B0_04", "B0_05",
                # Block 1: Economy ↔ Exploration
                "B1_01", "B1_02", "B1_03", "B1_04", "B1_05",
                # Block 2: Protection ↔ Contact
                "B2_01", "B2_02", "B2_03", "B2_04", "B2_05",
                # Block 3: Retention ↔ Movement
                "B3_01", "B3_02", "B3_03", "B3_04", "B3_05",
                # Block 4: Survival ↔ Development
                "B4_01", "B4_02", "B4_03", "B4_04", "B4_05",
            ]
            next_screen_id = full_sequence[screens_shown]
            routing_decision = {
                "action": "next_screen",
                "next_screen": {"screen_id": next_screen_id}
            }
        elif screens_shown >= 25:
            # Hard limit reached
            routing_decision = {"action": "finalize"}
        else:
            # Ask Claude for routing decision
            routing_decision = await claude_orchestrator.select_next_screen(state)
        
        action = routing_decision.get("action", "next_screen")
        screen_response = None
        
        if action == "next_screen":
            next_screen_info = routing_decision.get("next_screen", {})
            screen_id = next_screen_info.get("screen_id")
            
            if screen_id:
                try:
                    screen = screen_bank_loader.get_screen_by_id(screen_id)
                    screen_response = _format_screen_response(screen)
                except ScreenNotFoundError:
                    logger.warning(
                        "screen_not_found_in_routing",
                        session_id=session_id,
                        screen_id=screen_id,
                    )
                    action = "finalize"
        
        return NextScreenResponse(
            session_id=session_id,
            screen=screen_response,
            action=action,
            progress={
                "screens_completed": session.screens_completed,
                "estimated_remaining": max(0, 15 - session.screens_completed),
            }
        )
    
    except SessionNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )
    except SessionExpiredError:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Session has expired"
        )
    except SessionAlreadyCompletedError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Session already completed"
        )


@router.post(
    "/{session_id}/response",
    response_model=ClientResponseResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Session not found"},
        410: {"model": ErrorResponse, "description": "Session expired"},
        400: {"model": ErrorResponse, "description": "Invalid session state or response"},
    }
)
async def submit_response(
    session_id: str,
    data: ClientResponseRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Submit client's response to a screen.
    Updates session state and determines next action.
    """
    logger.info(
        "submit_response_request",
        session_id=session_id,
        screen_id=data.screen_id,
        response_value=data.response_value,
    )
    
    session_manager = SessionManager(db)
    
    try:
        # Get screen data for metadata (continuum, context, screen_type)
        screen_data = None
        try:
            screen = screen_bank_loader.get_screen_by_id(data.screen_id)
            screen_data = {
                "continuum": screen.get("continuum"),
                "context": screen.get("context"),
                "screen_type": screen.get("screen_type", "slider"),
                "block": screen.get("block"),
            }
        except ScreenNotFoundError:
            logger.warning(
                "screen_not_found_for_response",
                session_id=session_id,
                screen_id=data.screen_id,
            )
        
        # Update session state with response
        session = await session_manager.update_session_state(
            session_id=session_id,
            screen_id=data.screen_id,
            response_value=data.response_value,
            screen_data=screen_data,
        )
        
        # Determine next action (simplified - Claude will decide in get_next_screen)
        next_action = "next_screen"
        if session.screens_completed >= 25:  # Hard limit
            next_action = "finalize"
        
        return ClientResponseResponse(
            session_id=session_id,
            screen_id=data.screen_id,
            accepted=True,
            next_action=next_action,
        )
        
    except SessionNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )
    except SessionExpiredError:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Session has expired"
        )
    except SessionNotStartedError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session not started. Call /start first."
        )
    except SessionAlreadyCompletedError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Session already completed"
        )


@router.post(
    "/{session_id}/finalize",
    response_model=FinalizeResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Session not found"},
        400: {"model": ErrorResponse, "description": "Invalid session state"},
    }
)
async def finalize_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Finalize session and generate screening output.
    Uses Claude API to generate comprehensive output.
    """
    logger.info("finalize_session_request", session_id=session_id)
    
    session_manager = SessionManager(db)
    
    try:
        # Get session first to get specialist info
        session = await session_manager.get_session(session_id)
        
        # Get specialist telegram_id
        specialist_telegram_id = None
        if session.specialist_id:
            from sqlalchemy import select
            from app.models.specialist import Specialist
            stmt = select(Specialist).where(Specialist.id == session.specialist_id)
            result = await db.execute(stmt)
            specialist = result.scalar_one_or_none()
            if specialist:
                specialist_telegram_id = specialist.telegram_id
        
        # Get session state
        state = await session_manager.get_session_state(session_id)
        
        # Generate output using Claude
        output = await claude_orchestrator.generate_output(state)
        
        # Finalize session
        session = await session_manager.finalize_session(
            session_id=session_id,
            screening_output=output.get("screening_output", {}),
            interview_protocol=output.get("interview_protocol", {}),
        )
        
        return FinalizeResponse(
            session_id=session_id,
            status=session.status,
            duration_minutes=session.duration_minutes or 0,
            screens_completed=session.screens_completed,
            output_available=True,
            specialist_telegram_id=specialist_telegram_id,
        )
        
    except SessionNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )
    except InvalidSessionStateError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get(
    "/{session_id}/output",
    response_model=OutputResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Session or output not found"},
    }
)
async def get_output(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Get screening output for a completed session.
    
    Marks output as viewed by specialist.
    """
    logger.info("get_output_request", session_id=session_id)
    
    session_manager = SessionManager(db)
    
    try:
        output = await session_manager.get_output(session_id)
        
        return OutputResponse(
            session_id=session_id,
            screening_output=json.loads(output.screening_output) if output.screening_output else {},
            interview_protocol=json.loads(output.interview_protocol) if output.interview_protocol else {},
            created_at=output.created_at,
            viewed_at=output.viewed_by_specialist_at,
        )
    
    except SessionNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session or output not found"
        )


@router.get(
    "/{session_id}/status",
    response_model=SessionStatusResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Session not found"},
    }
)
async def get_session_status(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get current status of a session."""
    logger.debug("get_session_status_request", session_id=session_id)
    
    session_manager = SessionManager(db)
    
    try:
        session = await session_manager.get_session(session_id)
        
        return SessionStatusResponse(
            session_id=session.session_id,
            status=session.status,
            screens_completed=session.screens_completed,
            created_at=session.created_at,
            started_at=session.started_at,
            completed_at=session.completed_at,
            expires_at=session.expires_at,
        )
    
    except SessionNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )
