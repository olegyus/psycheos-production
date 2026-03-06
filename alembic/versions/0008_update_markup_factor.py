"""update markup_factor from 1.2 to 4.5

Revision ID: 0008
Revises: 0007
Create Date: 2026-03-06

Increases the AI cost markup from 20% to 4.5× to reflect production pricing.
The markup_factor column in ai_rates is applied as:
  stars_charged = base_cost_usd * markup_factor * exchange_rate
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE ai_rates SET markup_factor = 4.500")


def downgrade() -> None:
    op.execute("UPDATE ai_rates SET markup_factor = 1.200")
