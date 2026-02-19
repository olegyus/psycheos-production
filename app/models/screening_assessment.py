"""
ScreeningAssessment — результат скрининговой сессии Screen v2.

Хранит всё состояние сессии: фазы, векторы, матрицу напряжений,
историю ответов и финальный отчёт.
"""
import uuid
from datetime import datetime
from sqlalchemy import (
    BigInteger, Boolean, Float, Integer, String, Text,
    DateTime, ForeignKey, Index, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ScreeningAssessment(Base):
    __tablename__ = "screening_assessments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    # --- Relations ---
    context_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contexts.context_id"),
        nullable=False,
    )
    specialist_user_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )  # telegram_id специалиста
    client_chat_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )  # telegram chat_id клиента (заполняется при verify)

    # --- Session state ---
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="created"
    )  # created | in_progress | completed | expired
    phase: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )  # 0=не начато, 1, 2, 3
    phase1_completed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    phase2_questions: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )  # кол-во заданных вопросов в фазе 2
    phase3_questions: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )  # кол-во заданных вопросов в фазе 3

    # --- Scoring vectors ---
    axis_vector: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )  # оси: {axis_id: score}
    layer_vector: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )  # слои психики: {layer_id: score}
    tension_matrix: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )  # матрица напряжений: {cell_id: value}
    rigidity: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )  # ригидность по осям: {axis_id: rigidity_score}

    # --- Confidence & ambiguity ---
    confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    ambiguity_zones: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )  # список осей/зон с высокой неопределённостью
    dominant_cells: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )  # топ ячеек матрицы по интенсивности

    # --- History ---
    response_history: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )  # [{question_id, answer, score, timestamp}, ...]

    # --- Output ---
    report_json: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True
    )  # структурированный отчёт
    report_text: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # текстовое резюме для специалиста

    # --- Timestamps ---
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # когда клиент открыл сессию
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # TTL сессии

    # --- Link ---
    link_token_jti: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("link_tokens.jti"),
        nullable=True,
    )

    __table_args__ = (
        Index("idx_screening_assessments_context", "context_id"),
        Index("idx_screening_assessments_status", "status"),
    )
