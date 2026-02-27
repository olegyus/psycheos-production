"""add screening_assessment table

Revision ID: 0005
Revises: 0004
Create Date: 2026-02-27

The screening_assessment table was historically created via Base.metadata.create_all
on first startup (before Alembic tracked it).  This migration adds it to the
migration chain so future schema changes can be tracked properly.

upgrade() is idempotent: if the table already exists (created by create_all
on a running instance) it is skipped entirely.  downgrade() drops it unconditionally.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Guard: skip creation if the table already exists (created by create_all
    # before this migration was written).
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "screening_assessment" in inspector.get_table_names():
        return

    op.create_table(
        "screening_assessment",
        # ── Primary key ───────────────────────────────────────────────────────
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        # ── Linkage ───────────────────────────────────────────────────────────
        sa.Column("context_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("specialist_user_id", sa.BigInteger(), nullable=False),
        sa.Column("client_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("link_token_jti", postgresql.UUID(as_uuid=True), nullable=True),
        # ── Session control ───────────────────────────────────────────────────
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'created'"),
        ),
        sa.Column(
            "phase",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "phase1_completed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "phase2_questions",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "phase3_questions",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        # ── Vector model ──────────────────────────────────────────────────────
        sa.Column(
            "axis_vector",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "layer_vector",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "tension_matrix",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "rigidity",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "confidence",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0.0"),
        ),
        sa.Column(
            "ambiguity_zones",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "dominant_cells",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "response_history",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        # ── Final report ──────────────────────────────────────────────────────
        sa.Column(
            "report_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("report_text", sa.Text(), nullable=True),
        # ── Timestamps ────────────────────────────────────────────────────────
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        # ── Constraints ───────────────────────────────────────────────────────
        sa.ForeignKeyConstraint(
            ["context_id"],
            ["contexts.context_id"],
        ),
        sa.ForeignKeyConstraint(
            ["link_token_jti"],
            ["link_tokens.jti"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_screening_assessment_context",
        "screening_assessment",
        ["context_id"],
    )
    op.create_index(
        "idx_screening_assessment_status",
        "screening_assessment",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("idx_screening_assessment_status", table_name="screening_assessment")
    op.drop_index("idx_screening_assessment_context", table_name="screening_assessment")
    op.drop_table("screening_assessment")
