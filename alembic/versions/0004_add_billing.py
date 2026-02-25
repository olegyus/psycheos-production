"""add billing tables (wallets, usage_ledger, ai_rates) and billing columns on jobs

Revision ID: 0004
Revises: 0003
Create Date: 2026-02-25

Phase 7 — Telegram Stars billing:
  wallets       — per-user Stars balance (two-phase: balance / reserved)
  usage_ledger  — immutable audit log of every financial event
  ai_rates      — admin-editable pre-calculated Stars price per operation
  jobs          — add stars_reserved, wallet_id columns
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── wallets ───────────────────────────────────────────────────────────────
    op.create_table(
        "wallets",
        sa.Column(
            "wallet_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "balance_stars",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "reserved_stars",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "lifetime_in",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "lifetime_out",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("wallet_id"),
        sa.UniqueConstraint("user_id", name="uq_wallets_user_id"),
    )
    op.create_index("idx_wallets_user_id", "wallets", ["user_id"])

    # ── usage_ledger ──────────────────────────────────────────────────────────
    op.create_table(
        "usage_ledger",
        sa.Column(
            "entry_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "wallet_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("stars", sa.Integer(), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("service_id", sa.String(32), nullable=True),
        sa.Column("operation", sa.String(60), nullable=True),
        sa.Column("payment_charge_id", sa.String(255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["wallet_id"], ["wallets.wallet_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("entry_id"),
    )
    op.create_index("idx_ledger_wallet_id", "usage_ledger", ["wallet_id"])
    op.create_index("idx_ledger_telegram_id", "usage_ledger", ["telegram_id"])
    op.create_index("idx_ledger_run_id", "usage_ledger", ["run_id"])

    # ── ai_rates ──────────────────────────────────────────────────────────────
    op.create_table(
        "ai_rates",
        sa.Column(
            "rate_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("service_id", sa.String(32), nullable=False),
        sa.Column("operation", sa.String(60), nullable=False),
        sa.Column("model", sa.String(60), nullable=False),
        sa.Column("input_tok_est", sa.Integer(), nullable=False),
        sa.Column("output_tok_est", sa.Integer(), nullable=False),
        sa.Column(
            "markup_factor",
            sa.Numeric(5, 3),
            server_default=sa.text("1.200"),
            nullable=False,
        ),
        sa.Column("stars_price", sa.SmallInteger(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("rate_id"),
        sa.UniqueConstraint(
            "service_id", "operation", name="uq_ai_rates_service_operation"
        ),
    )

    # Seed default rates
    # Formula: ceil((in_tok × in_price + out_tok × out_price + total_tok × 2/1M) × 1.2 / 0.01)
    # Sonnet 4.5: $3/M in, $15/M out  |  Haiku 4.5: $0.80/M in, $4/M out
    op.execute(
        sa.text(
            """
            INSERT INTO ai_rates
                (service_id, operation, model, input_tok_est, output_tok_est, markup_factor, stars_price, note)
            VALUES
                ('interpretator',   'session',     'claude-sonnet-4-5-20250929', 3000,  2500, 1.200, 20,
                 'Photo analysis + intake + report (Sonnet 4.5)'),
                ('conceptualizator','session',     'claude-sonnet-4-5-20250929', 2500,  2000, 1.200, 12,
                 'Hypothesis extraction + 3-layer output (Sonnet 4.5)'),
                ('simulator',       'session',     'claude-sonnet-4-5-20250929', 3000,  3000, 1.200, 15,
                 'Simulation launch + report (Sonnet 4.5)'),
                ('simulator',       'active_turn', 'claude-sonnet-4-5-20250929',  400,   400, 1.200,  3,
                 'Single active-turn reply during simulation (Sonnet 4.5)'),
                ('pro',             'reference',   'claude-haiku-4-5-20251001',   600,   400, 1.200,  1,
                 'Single reference-chat Q&A turn (Haiku 4.5)')
            ON CONFLICT (service_id, operation) DO NOTHING
            """
        )
    )

    # ── jobs: add billing columns ──────────────────────────────────────────────
    op.add_column(
        "jobs",
        sa.Column("stars_reserved", sa.SmallInteger(), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "wallet_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_jobs_wallet_id",
        "jobs",
        "wallets",
        ["wallet_id"],
        ["wallet_id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_jobs_wallet_id", "jobs", type_="foreignkey")
    op.drop_column("jobs", "wallet_id")
    op.drop_column("jobs", "stars_reserved")

    op.drop_table("ai_rates")

    op.drop_index("idx_ledger_run_id", table_name="usage_ledger")
    op.drop_index("idx_ledger_telegram_id", table_name="usage_ledger")
    op.drop_index("idx_ledger_wallet_id", table_name="usage_ledger")
    op.drop_table("usage_ledger")

    op.drop_index("idx_wallets_user_id", table_name="wallets")
    op.drop_table("wallets")
