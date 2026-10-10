"""Preserve unit records when removing a unit from the active workspace.

Revision ID: 6a2e749c18b3
Revises: 27b303699f69
"""
from alembic import op
import sqlalchemy as sa

revision = '6a2e749c18b3'
down_revision = '27b303699f69'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('dormitory_units', sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column('dormitory_units', 'archived_at')
