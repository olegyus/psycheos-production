"""add artifacts table

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-20

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
        "artifacts",
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("context_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_id", sa.String(32), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("specialist_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["context_id"], ["contexts.context_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("artifact_id"),
        sa.UniqueConstraint("run_id", "service_id", name="uq_artifacts_run_service"),
    )
    op.create_index(
        "idx_artifacts_context_time", "artifacts", ["context_id", "created_at"]
    )
    op.create_index(
        "idx_artifacts_specialist", "artifacts", ["specialist_telegram_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("idx_artifacts_specialist", table_name="artifacts")
    op.drop_index("idx_artifacts_context_time", table_name="artifacts")
    op.drop_table("artifacts")
