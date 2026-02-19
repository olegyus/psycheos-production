"""
Custom exceptions for PsycheOS application.
"""

from typing import Any


class PsycheOSError(Exception):
    """Base exception for PsycheOS application."""
    
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


# === Specialist Exceptions ===

class SpecialistNotFoundError(PsycheOSError):
    """Raised when specialist is not found."""
    
    def __init__(self, identifier: str | int):
        super().__init__(
            message=f"Specialist not found: {identifier}",
            details={"identifier": identifier}
        )


class SpecialistAlreadyExistsError(PsycheOSError):
    """Raised when trying to register an existing specialist."""
    
    def __init__(self, telegram_id: int):
        super().__init__(
            message=f"Specialist already registered: {telegram_id}",
            details={"telegram_id": telegram_id}
        )


# === Token Exceptions ===

class InsufficientTokensError(PsycheOSError):
    """Raised when specialist doesn't have enough tokens."""
    
    def __init__(self, required: int, available: int, specialist_id: int):
        super().__init__(
            message=f"Insufficient tokens: required {required}, available {available}",
            details={
                "required": required,
                "available": available,
                "specialist_id": specialist_id
            }
        )


class InvalidTokenOperationError(PsycheOSError):
    """Raised when token operation is invalid."""
    pass


# === Session Exceptions ===

class SessionNotFoundError(PsycheOSError):
    """Raised when screening session is not found."""
    
    def __init__(self, session_id: str):
        super().__init__(
            message=f"Session not found: {session_id}",
            details={"session_id": session_id}
        )


class SessionExpiredError(PsycheOSError):
    """Raised when session has expired."""
    
    def __init__(self, session_id: str):
        super().__init__(
            message=f"Session has expired: {session_id}",
            details={"session_id": session_id}
        )


class SessionAlreadyCompletedError(PsycheOSError):
    """Raised when trying to modify a completed session."""
    
    def __init__(self, session_id: str):
        super().__init__(
            message=f"Session already completed: {session_id}",
            details={"session_id": session_id}
        )


class SessionNotStartedError(PsycheOSError):
    """Raised when session has not been started yet."""
    
    def __init__(self, session_id: str):
        super().__init__(
            message=f"Session not started: {session_id}",
            details={"session_id": session_id}
        )


class InvalidSessionStateError(PsycheOSError):
    """Raised when session state is invalid for the operation."""
    
    def __init__(self, session_id: str, current_status: str, expected_status: str):
        super().__init__(
            message=f"Invalid session state: expected {expected_status}, got {current_status}",
            details={
                "session_id": session_id,
                "current_status": current_status,
                "expected_status": expected_status
            }
        )


# === Screen Exceptions ===

class ScreenNotFoundError(PsycheOSError):
    """Raised when screen is not found in screen bank."""
    
    def __init__(self, screen_id: str):
        super().__init__(
            message=f"Screen not found: {screen_id}",
            details={"screen_id": screen_id}
        )


class InvalidScreenResponseError(PsycheOSError):
    """Raised when screen response is invalid."""
    
    def __init__(self, screen_id: str, response: Any, reason: str):
        super().__init__(
            message=f"Invalid response for screen {screen_id}: {reason}",
            details={
                "screen_id": screen_id,
                "response": response,
                "reason": reason
            }
        )


# === Claude API Exceptions ===

class ClaudeAPIError(PsycheOSError):
    """Raised when Claude API request fails."""
    
    def __init__(self, message: str, original_error: Exception | None = None):
        super().__init__(
            message=message,
            details={"original_error": str(original_error) if original_error else None}
        )
        self.original_error = original_error


class ClaudeRoutingError(PsycheOSError):
    """Raised when Claude routing decision is invalid."""
    
    def __init__(self, message: str, response: Any):
        super().__init__(
            message=message,
            details={"response": response}
        )


class ClaudeOutputGenerationError(PsycheOSError):
    """Raised when Claude fails to generate output."""
    
    def __init__(self, message: str, response: Any = None):
        super().__init__(
            message=message,
            details={"response": response}
        )


# === Data Loading Exceptions ===

class DataLoadingError(PsycheOSError):
    """Raised when loading data files fails."""
    
    def __init__(self, filename: str, reason: str):
        super().__init__(
            message=f"Failed to load {filename}: {reason}",
            details={"filename": filename, "reason": reason}
        )
