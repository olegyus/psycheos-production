"""
Screening session model.
"""

from datetime import datetime
from enum import Enum
from sqlalchemy import Integer, String, DateTime, ForeignKey, Text, BigInteger
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import TYPE_CHECKING

from app.database import Base

if TYPE_CHECKING:
    from app.models.specialist import Specialist
    from app.models.output import ScreeningOutput
    from app.models.transaction import TokenTransaction


class SessionStatus(str, Enum):
    """Possible session statuses."""
    CREATED = "created"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ScreeningSession(Base):
    """Screening session for a client."""
    
    __tablename__ = "screening_sessions"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    
    # Specialist who created the session
    specialist_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("specialists.id"), nullable=False
    )
    
    # Client info
    client_identifier: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    
    # Session status
    status: Mapped[str] = mapped_column(
        String(50), default=SessionStatus.CREATED.value, nullable=False
    )
    
    # Session state (JSON string - full Session State object)
    session_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    # Progress tracking
    screens_completed: Mapped[int] = mapped_column(Integer, default=0)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    
    # Relationships
    specialist: Mapped["Specialist"] = relationship(
        "Specialist", back_populates="sessions"
    )
    output: Mapped["ScreeningOutput | None"] = relationship(
        "ScreeningOutput", back_populates="session", uselist=False
    )
    transactions: Mapped[list["TokenTransaction"]] = relationship(
        "TokenTransaction", back_populates="session"
    )
    
    def __repr__(self) -> str:
        return f"<ScreeningSession(session_id={self.session_id}, status={self.status})>"
    
    @property
    def is_active(self) -> bool:
        """Check if session is active (can accept responses)."""
        return self.status == SessionStatus.IN_PROGRESS.value
    
    @property
    def is_completed(self) -> bool:
        """Check if session is completed."""
        return self.status == SessionStatus.COMPLETED.value
    
    @property
    def is_expired(self) -> bool:
        """Check if session has expired."""
        if self.status == SessionStatus.EXPIRED.value:
            return True
        if self.expires_at and datetime.utcnow() > self.expires_at:
            return True
        return False
