"""create screening_assessments table

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "screening_assessments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        # Relations
        sa.Column("context_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("specialist_user_id", sa.BigInteger(), nullable=False),
        sa.Column("client_chat_id", sa.BigInteger(), nullable=True),
        # Session state
        sa.Column("status", sa.String(20), nullable=False, server_default="created"),
        sa.Column("phase", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("phase1_completed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("phase2_questions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("phase3_questions", sa.Integer(), nullable=False, server_default="0"),
        # Scoring vectors
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
        # Confidence & ambiguity
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.0"),
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
        # History
        sa.Column(
            "response_history",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        # Output
        sa.Column(
            "report_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("report_text", sa.Text(), nullable=True),
        # Timestamps
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        # Link token
        sa.Column("link_token_jti", postgresql.UUID(as_uuid=True), nullable=True),
        # Constraints
        sa.ForeignKeyConstraint(["context_id"], ["contexts.context_id"]),
        sa.ForeignKeyConstraint(["link_token_jti"], ["link_tokens.jti"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_screening_assessments_context",
        "screening_assessments",
        ["context_id"],
    )
    op.create_index(
        "idx_screening_assessments_status",
        "screening_assessments",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("idx_screening_assessments_status", table_name="screening_assessments")
    op.drop_index("idx_screening_assessments_context", table_name="screening_assessments")
    op.drop_table("screening_assessments")
