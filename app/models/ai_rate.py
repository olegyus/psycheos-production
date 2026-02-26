"""
AIRate — admin-editable pricing table for tool operations.

Pre-calculated Stars price per (service_id, operation).
Worker uses the stored stars_price (no runtime calculation needed).

Formula at rate-setting time:
  stars_price = ceil(
      (input_tok_est × in_$/tok + output_tok_est × out_$/tok
       + (input_tok_est + output_tok_est) × 2/1_000_000)
      × markup_factor / 0.01
  )
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, Numeric, SmallInteger, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AIRate(Base):
    __tablename__ = "ai_rates"

    rate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    service_id: Mapped[str] = mapped_column(String(32), nullable=False)
    operation: Mapped[str] = mapped_column(String(60), nullable=False)

    model: Mapped[str] = mapped_column(String(60), nullable=False)
    input_tok_est: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tok_est: Mapped[int] = mapped_column(Integer, nullable=False)
    markup_factor: Mapped[float] = mapped_column(
        Numeric(5, 3), nullable=False, server_default=text("1.200")
    )

    # Pre-calculated Stars price (stored result of the formula above)
    stars_price: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=datetime.utcnow,
    )

    __table_args__ = (
        # One price record per (service, operation) pair
        UniqueConstraint("service_id", "operation", name="uq_ai_rates_service_operation"),
    )
