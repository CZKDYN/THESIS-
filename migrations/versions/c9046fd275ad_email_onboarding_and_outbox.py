"""Add hashed account links, encrypted email outbox, and preferences.

Revision ID: c9046fd275ad
Revises: b782410cdf65
"""
from alembic import op
import sqlalchemy as sa

revision = 'c9046fd275ad'
down_revision = 'b782410cdf65'
branch_labels = None
depends_on = None


def upgrade():
    # Existing active/inactive accounts, password hashes and financial data stay unchanged.
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column('activation_status', sa.String(16), nullable=False, server_default='active'))
        batch.add_column(sa.Column('email_verified_at', sa.DateTime(timezone=True)))
        batch.add_column(sa.Column('email_balance_alerts', sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(sa.Column('email_load_updates', sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.create_check_constraint('valid_activation_status', "activation_status IN ('pending', 'active')")
    op.create_table('account_tokens',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('unit_id', sa.Integer(), sa.ForeignKey('dormitory_units.id')),
        sa.Column('purpose', sa.String(16), nullable=False),
        sa.Column('token_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('email_snapshot', sa.String(254), nullable=False),
        sa.Column('session_version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True)),
        sa.Column('revoked_at', sa.DateTime(timezone=True)),
        sa.CheckConstraint("purpose IN ('activation', 'reset')", name='valid_token_purpose'))
    op.create_index('ix_account_tokens_user_id', 'account_tokens', ['user_id'])
    op.create_table('email_outbox',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('unit_id', sa.Integer(), sa.ForeignKey('dormitory_units.id')),
        sa.Column('token_id', sa.Integer(), sa.ForeignKey('account_tokens.id')),
        sa.Column('kind', sa.String(40), nullable=False),
        sa.Column('dedupe_key', sa.String(200), nullable=False, unique=True),
        sa.Column('payload_encrypted', sa.Text()),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('error_code', sa.String(50)),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('lease_id', sa.String(36)),
        sa.Column('leased_until', sa.DateTime(timezone=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True)),
        sa.Column('provider_message_id', sa.String(128)),
        sa.CheckConstraint("status IN ('pending', 'sending', 'sent', 'failed', 'cancelled', 'test')", name='valid_email_status'))
    op.create_index('ix_email_outbox_user_id', 'email_outbox', ['user_id'])
    op.create_index('ix_email_outbox_next_attempt_at', 'email_outbox', ['next_attempt_at'])
    op.create_table('email_rate_limits',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('limit_key', sa.String(64), nullable=False),
        sa.Column('window_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('hits', sa.Integer(), nullable=False),
        sa.UniqueConstraint('limit_key', 'window_start', name='email_limit_window_unique'))


def downgrade():
    op.drop_table('email_rate_limits')
    op.drop_table('email_outbox')
    op.drop_table('account_tokens')
    with op.batch_alter_table('users') as batch:
        batch.drop_constraint('valid_activation_status', type_='check')
        for name in ('activation_status', 'email_verified_at', 'email_balance_alerts', 'email_load_updates'):
            batch.drop_column(name)
