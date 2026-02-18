"""create link_tokens table

Revision ID: 0001
Revises:
Create Date: 2026-02-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "link_tokens",
        sa.Column(
            "jti",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_id", sa.String(32), nullable=False),
        sa.Column("context_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("subject_id", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["context_id"], ["contexts.context_id"]),
        sa.PrimaryKeyConstraint("jti"),
        sa.UniqueConstraint("service_id", "run_id", name="uq_link_tokens_service_run"),
    )
    op.create_index("idx_link_tokens_context", "link_tokens", ["context_id"])
    op.create_index("idx_link_tokens_expires", "link_tokens", ["expires_at"])


def downgrade() -> None:
    op.drop_index("idx_link_tokens_expires", table_name="link_tokens")
    op.drop_index("idx_link_tokens_context", table_name="link_tokens")
    op.drop_table("link_tokens")
