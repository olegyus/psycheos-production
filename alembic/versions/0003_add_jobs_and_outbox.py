"""add jobs and outbox_messages tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-02-20

Two tables for the async Worker + Outbox pattern (Phase 6):
  jobs            — task queue for Claude API calls
  outbox_messages — Telegram API calls queued for delivery
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── jobs ─────────────────────────────────────────────────────────────────
    op.create_table(
        "jobs",
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("job_type", sa.String(60), nullable=False),
        sa.Column("bot_id", sa.String(50), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("context_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(20),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "priority",
            sa.SmallInteger(),
            server_default=sa.text("5"),
            nullable=False,
        ),
        sa.Column(
            "attempts",
            sa.SmallInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "max_attempts",
            sa.SmallInteger(),
            server_default=sa.text("3"),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "scheduled_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_index(
        "idx_jobs_queue",
        "jobs",
        ["priority", "scheduled_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "idx_jobs_chat",
        "jobs",
        ["bot_id", "chat_id", "status"],
    )

    # ── outbox_messages ───────────────────────────────────────────────────────
    op.create_table(
        "outbox_messages",
        sa.Column(
            "msg_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("bot_id", sa.String(50), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("tg_method", sa.String(50), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(20),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "attempts",
            sa.SmallInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "max_attempts",
            sa.SmallInteger(),
            server_default=sa.text("5"),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "seq",
            sa.SmallInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("msg_id"),
    )
    op.create_index(
        "idx_outbox_pending",
        "outbox_messages",
        ["created_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "idx_outbox_chat_seq",
        "outbox_messages",
        ["bot_id", "chat_id", "seq", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_outbox_chat_seq", table_name="outbox_messages")
    op.drop_index("idx_outbox_pending", table_name="outbox_messages")
    op.drop_table("outbox_messages")
    op.drop_index("idx_jobs_chat", table_name="jobs")
    op.drop_index("idx_jobs_queue", table_name="jobs")
    op.drop_table("jobs")
