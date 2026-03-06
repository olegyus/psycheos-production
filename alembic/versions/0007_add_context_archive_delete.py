"""add archived_at and deleted_at to contexts

Revision ID: 0007
Revises: 0006
Create Date: 2026-03-06

Adds soft-archive and soft-delete timestamps to the contexts table.
- archived_at IS NULL  → active case (shown in main list)
- archived_at IS NOT NULL AND deleted_at IS NULL → archived case
- deleted_at IS NOT NULL → deleted, never shown

Backfills archived_at for rows that were already marked status='archived'
via the legacy status field, using updated_at as the archive timestamp.
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "contexts",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "contexts",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Backfill: cases already archived via the status field get archived_at set
    op.execute(
        "UPDATE contexts SET archived_at = updated_at WHERE status = 'archived'"
    )
    op.create_index("idx_contexts_archived_at", "contexts", ["archived_at"])
    op.create_index("idx_contexts_deleted_at", "contexts", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("idx_contexts_deleted_at", table_name="contexts")
    op.drop_index("idx_contexts_archived_at", table_name="contexts")
    op.drop_column("contexts", "deleted_at")
    op.drop_column("contexts", "archived_at")
