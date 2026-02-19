"""
Screening output model - stores final results.
"""

from datetime import datetime
from sqlalchemy import Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import TYPE_CHECKING

from app.database import Base

if TYPE_CHECKING:
    from app.models.session import ScreeningSession


class ScreeningOutput(Base):
    """Screening output - results of completed screening."""
    
    __tablename__ = "screening_outputs"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    
    # Link to session
    session_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("screening_sessions.session_id"),
        unique=True,
        nullable=False
    )
    
    # Results (JSON strings)
    screening_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    interview_protocol: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    viewed_by_specialist_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    
    # Relationships
    session: Mapped["ScreeningSession"] = relationship(
        "ScreeningSession", back_populates="output"
    )
    
    def __repr__(self) -> str:
        return f"<ScreeningOutput(session_id={self.session_id})>"
    
    def mark_as_viewed(self) -> None:
        """Mark output as viewed by specialist."""
        if self.viewed_by_specialist_at is None:
            self.viewed_by_specialist_at = datetime.utcnow()
