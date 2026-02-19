"""
Pydantic schemas for API request/response validation.
"""

from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict
from typing import Any


# === Base Schemas ===

class TimestampMixin(BaseModel):
    """Mixin for timestamp fields."""
    created_at: datetime
    updated_at: datetime | None = None


# === Specialist Schemas ===

class SpecialistCreate(BaseModel):
    """Schema for creating a specialist."""
    telegram_id: int = Field(..., description="Telegram user ID")
    username: str | None = Field(None, description="Telegram username")
    name: str | None = Field(None, description="Specialist name")
    email: str | None = Field(None, description="Email address")


class SpecialistResponse(BaseModel):
    """Schema for specialist response."""
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    telegram_id: int
    username: str | None
    name: str | None
    email: str | None
    tokens_balance: int
    tokens_spent: int
    tokens_purchased: int
    created_at: datetime
    last_active_at: datetime | None


class SpecialistBalance(BaseModel):
    """Schema for specialist balance."""
    telegram_id: int
    tokens_balance: int
    tokens_spent: int
    tokens_purchased: int


class TokenAddRequest(BaseModel):
    """Schema for adding tokens."""
    amount: int = Field(..., gt=0, description="Number of tokens to add")
    description: str | None = Field(None, description="Description of the transaction")


class TokenAddResponse(BaseModel):
    """Response after adding tokens."""
    telegram_id: int
    tokens_added: int
    new_balance: int
    transaction_id: int


# === Transaction Schemas ===

class TransactionResponse(BaseModel):
    """Schema for transaction response."""
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    amount: int
    transaction_type: str
    description: str | None
    session_id: str | None
    created_at: datetime


class TransactionListResponse(BaseModel):
    """Schema for transaction list response."""
    transactions: list[TransactionResponse]
    total: int


# === Session Schemas ===

class SessionCreateRequest(BaseModel):
    """Schema for creating a session."""
    specialist_telegram_id: int = Field(..., description="Specialist's Telegram ID")
    client_identifier: str | None = Field(None, description="Optional client identifier")


class SessionCreateResponse(BaseModel):
    """Response after creating a session."""
    session_id: str
    deep_link: str
    expires_at: datetime
    tokens_spent: int


class SessionStartResponse(BaseModel):
    """Response after starting a session."""
    session_id: str
    status: str
    first_screen: dict[str, Any] | None = None


class SessionStatusResponse(BaseModel):
    """Schema for session status."""
    session_id: str
    status: str
    screens_completed: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    expires_at: datetime | None


class ScreenResponse(BaseModel):
    """Schema for screen data."""
    screen_id: str
    screen_type: str  # slider, forced_choice, recognition, trigger
    stimulus: dict[str, str]  # {situation, question}
    response_format: dict[str, Any]  # {type, left_anchor, right_anchor, scale} или {type, options}
    continuum: str | None = None
    context: str | None = None
    load_level: str | None = None


class NextScreenResponse(BaseModel):
    """Response with next screen."""
    session_id: str
    screen: ScreenResponse | None
    action: str  # "next_screen" or "finalize"
    progress: dict[str, Any]


class ClientResponseRequest(BaseModel):
    """Schema for client response to a screen."""
    screen_id: str = Field(..., description="ID of the screen being answered")
    response_value: int | str = Field(..., description="Client's response (0-100 or option)")


class ClientResponseResponse(BaseModel):
    """Response after processing client answer."""
    session_id: str
    screen_id: str
    accepted: bool
    next_action: str  # "next_screen" or "finalize"


class FinalizeResponse(BaseModel):
    """Response after finalizing session."""
    session_id: str
    status: str
    duration_minutes: int
    screens_completed: int
    output_available: bool
    specialist_telegram_id: int | None = None


class OutputResponse(BaseModel):
    """Schema for screening output."""
    session_id: str
    screening_output: dict[str, Any]
    interview_protocol: dict[str, Any]
    created_at: datetime
    viewed_at: datetime | None


# === Health Check ===

class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    version: str
    database: str
    artifacts_loaded: bool


# === Error Responses ===

class ErrorResponse(BaseModel):
    """Standard error response."""
    detail: str
    error_type: str | None = None
    details: dict[str, Any] | None = None
