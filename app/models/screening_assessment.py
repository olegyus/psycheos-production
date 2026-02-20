"""ScreeningAssessment — per-client screening session for Screen v2.

One record per client screening run; linked to a specialist's Context.
All vector/matrix data is stored as JSONB so the engine can read/write
it directly without schema migrations on every structural change.
"""
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Float, Index, Integer, String, Text, text
from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ScreeningAssessment(Base):
    __tablename__ = "screening_assessment"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    # Linkage
    context_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contexts.context_id"),
        nullable=False,
    )
    specialist_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    client_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    link_token_jti: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("link_tokens.jti"),
        nullable=True,
    )

    # Session control
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="created"
    )  # created | in_progress | completed | expired
    phase: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    phase1_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    phase2_questions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    phase3_questions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Vector model (updated after every response)
    axis_vector: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    layer_vector: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    tension_matrix: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    rigidity: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    ambiguity_zones: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    dominant_cells: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    # Full response log — list of {screen_id, axis_weights, layer_weights, …}
    response_history: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    # Final report
    report_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    report_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("idx_screening_assessment_context", "context_id"),
        Index("idx_screening_assessment_status", "status"),
    )
