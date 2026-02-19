"""Initial migration - create all tables

Revision ID: 001_initial
Revises: 
Create Date: 2026-02-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '001_initial'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create specialists table
    op.create_table(
        'specialists',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('telegram_id', sa.BigInteger(), nullable=False),
        sa.Column('username', sa.String(length=255), nullable=True),
        sa.Column('name', sa.String(length=255), nullable=True),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('tokens_balance', sa.Integer(), nullable=True, default=0),
        sa.Column('tokens_spent', sa.Integer(), nullable=True, default=0),
        sa.Column('tokens_purchased', sa.Integer(), nullable=True, default=0),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('last_active_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('telegram_id')
    )
    op.create_index('ix_specialists_telegram_id', 'specialists', ['telegram_id'], unique=True)

    # Create screening_sessions table
    op.create_table(
        'screening_sessions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('session_id', sa.String(length=64), nullable=False),
        sa.Column('specialist_id', sa.Integer(), nullable=False),
        sa.Column('client_identifier', sa.String(length=255), nullable=True),
        sa.Column('client_telegram_id', sa.BigInteger(), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False, default='created'),
        sa.Column('session_state', sa.Text(), nullable=True),
        sa.Column('screens_completed', sa.Integer(), nullable=True, default=0),
        sa.Column('duration_minutes', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['specialist_id'], ['specialists.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('session_id')
    )
    op.create_index('ix_screening_sessions_session_id', 'screening_sessions', ['session_id'], unique=True)
    op.create_index('ix_screening_sessions_specialist_id', 'screening_sessions', ['specialist_id'], unique=False)
    op.create_index('ix_screening_sessions_status', 'screening_sessions', ['status'], unique=False)

    # Create screening_outputs table
    op.create_table(
        'screening_outputs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('session_id', sa.String(length=64), nullable=False),
        sa.Column('screening_output', sa.Text(), nullable=True),
        sa.Column('interview_protocol', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('viewed_by_specialist_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['session_id'], ['screening_sessions.session_id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('session_id')
    )
    op.create_index('ix_screening_outputs_session_id', 'screening_outputs', ['session_id'], unique=True)

    # Create token_transactions table
    op.create_table(
        'token_transactions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('specialist_id', sa.Integer(), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('transaction_type', sa.String(length=50), nullable=False),
        sa.Column('payment_amount_usd', sa.Float(), nullable=True),
        sa.Column('payment_provider', sa.String(length=100), nullable=True),
        sa.Column('payment_id', sa.String(length=255), nullable=True),
        sa.Column('session_id', sa.String(length=64), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['screening_sessions.session_id'], ),
        sa.ForeignKeyConstraint(['specialist_id'], ['specialists.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_token_transactions_specialist_id', 'token_transactions', ['specialist_id'], unique=False)
    op.create_index('ix_token_transactions_transaction_type', 'token_transactions', ['transaction_type'], unique=False)


def downgrade() -> None:
    # Drop tables in reverse order (respect foreign keys)
    op.drop_index('ix_token_transactions_transaction_type', table_name='token_transactions')
    op.drop_index('ix_token_transactions_specialist_id', table_name='token_transactions')
    op.drop_table('token_transactions')
    
    op.drop_index('ix_screening_outputs_session_id', table_name='screening_outputs')
    op.drop_table('screening_outputs')
    
    op.drop_index('ix_screening_sessions_status', table_name='screening_sessions')
    op.drop_index('ix_screening_sessions_specialist_id', table_name='screening_sessions')
    op.drop_index('ix_screening_sessions_session_id', table_name='screening_sessions')
    op.drop_table('screening_sessions')
    
    op.drop_index('ix_specialists_telegram_id', table_name='specialists')
    op.drop_table('specialists')
