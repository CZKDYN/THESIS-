"""Add utility rate provenance and validity; preserve every existing tariff/charge.

Revision ID: d21ae48f397c
Revises: c9046fd275ad
"""
from alembic import op
import sqlalchemy as sa

revision = 'd21ae48f397c'
down_revision = 'c9046fd275ad'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('electricity_rates') as batch:
        batch.add_column(sa.Column('utility_provider', sa.String(120)))
        batch.add_column(sa.Column('billing_period', sa.String(7)))
        batch.add_column(sa.Column('source_reference', sa.String(240)))
        batch.add_column(sa.Column('valid_until', sa.DateTime(timezone=True)))
        batch.add_column(sa.Column('configuration_key', sa.String(64)))
        batch.create_unique_constraint('utility_rate_configuration_unique', ['configuration_key'])
        batch.create_check_constraint('valid_rate_interval', 'valid_until IS NULL OR valid_until > effective_at')


def downgrade():
    with op.batch_alter_table('electricity_rates') as batch:
        batch.drop_constraint('valid_rate_interval', type_='check')
        batch.drop_constraint('utility_rate_configuration_unique', type_='unique')
        for name in ('configuration_key', 'valid_until', 'source_reference', 'billing_period', 'utility_provider'):
            batch.drop_column(name)
