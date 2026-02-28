"""add check constraint balance_stars >= 0

Revision ID: 0006
Revises: 0005
Create Date: 2026-02-28

Last-resort DB guard: even if the application-level race condition (two
concurrent reserve_stars calls) somehow bypasses the SELECT FOR UPDATE,
the DB will raise IntegrityError rather than letting balance_stars go
negative.
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_wallet_balance_non_negative",
        "wallets",
        "balance_stars >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_wallet_balance_non_negative",
        "wallets",
        type_="check",
    )
