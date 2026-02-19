"""
Token transaction model - tracks all token operations.
"""

from datetime import datetime
from enum import Enum
from sqlalchemy import Integer, String, DateTime, ForeignKey, Float, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import TYPE_CHECKING

from app.database import Base

if TYPE_CHECKING:
    from app.models.specialist import Specialist
    from app.models.session import ScreeningSession


class TransactionType(str, Enum):
    """Types of token transactions."""
    PURCHASE = "purchase"
    SPEND = "spend"
    REFUND = "refund"
    BONUS = "bonus"


class TokenTransaction(Base):
    """Token transaction - tracks token operations."""
    
    __tablename__ = "token_transactions"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    
    # Specialist
    specialist_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("specialists.id"), nullable=False
    )
    
    # Amount (+N for purchase/bonus, -1 for spend)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    
    # Transaction type
    transaction_type: Mapped[str] = mapped_column(String(50), nullable=False)
    
    # Payment info (for purchases)
    payment_amount_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    payment_provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    payment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    
    # Session (for spend transactions)
    session_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("screening_sessions.session_id"),
        nullable=True
    )
    
    # Description
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    
    # Relationships
    specialist: Mapped["Specialist"] = relationship(
        "Specialist", back_populates="transactions"
    )
    session: Mapped["ScreeningSession | None"] = relationship(
        "ScreeningSession", back_populates="transactions"
    )
    
    def __repr__(self) -> str:
        return f"<TokenTransaction(id={self.id}, type={self.transaction_type}, amount={self.amount})>"
