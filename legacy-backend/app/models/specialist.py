"""
Specialist (psychologist) model.
"""

from datetime import datetime
from sqlalchemy import Integer, String, DateTime, BigInteger
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import TYPE_CHECKING

from app.database import Base

if TYPE_CHECKING:
    from app.models.session import ScreeningSession
    from app.models.transaction import TokenTransaction


class Specialist(Base):
    """Specialist (psychologist) who creates screening sessions."""
    
    __tablename__ = "specialists"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    
    # Token balance
    tokens_balance: Mapped[int] = mapped_column(Integer, default=0)
    tokens_spent: Mapped[int] = mapped_column(Integer, default=0)
    tokens_purchased: Mapped[int] = mapped_column(Integer, default=0)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    
    # Relationships
    sessions: Mapped[list["ScreeningSession"]] = relationship(
        "ScreeningSession", back_populates="specialist", lazy="selectin"
    )
    transactions: Mapped[list["TokenTransaction"]] = relationship(
        "TokenTransaction", back_populates="specialist", lazy="selectin"
    )
    
    def __repr__(self) -> str:
        return f"<Specialist(id={self.id}, telegram_id={self.telegram_id}, name={self.name})>"
    
    def has_tokens(self, amount: int = 1) -> bool:
        """Check if specialist has enough tokens."""
        return self.tokens_balance >= amount
    
    def spend_tokens(self, amount: int = 1) -> bool:
        """Spend tokens from balance. Returns True if successful."""
        if not self.has_tokens(amount):
            return False
        self.tokens_balance -= amount
        self.tokens_spent += amount
        return True
    
    def add_tokens(self, amount: int) -> None:
        """Add tokens to balance."""
        self.tokens_balance += amount
        self.tokens_purchased += amount
