"""Store XGBoost artifacts and administrator training jobs in PostgreSQL.

Revision ID: a13f260be924
Revises: 6a2e749c18b3
"""
from alembic import op
import sqlalchemy as sa

revision = 'a13f260be924'
down_revision = '6a2e749c18b3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('forecast_models',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('unit_id', sa.Integer(), sa.ForeignKey('dormitory_units.id'), nullable=False),
        sa.Column('version', sa.String(64), nullable=False, unique=True),
        sa.Column('source', sa.String(16), nullable=False),
        sa.Column('model_json', sa.Text(), nullable=False),
        sa.Column('metadata_json', sa.JSON(), nullable=False),
        sa.Column('trained_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("source IN ('simulation', 'hardware')", name='valid_model_source'))
    op.create_index('ix_forecast_models_unit_id', 'forecast_models', ['unit_id'])
    op.create_table('forecast_jobs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('unit_id', sa.Integer(), sa.ForeignKey('dormitory_units.id'), nullable=False),
        sa.Column('requested_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('message', sa.String(300), nullable=False),
        sa.Column('model_version', sa.String(64)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True)),
        sa.Column('completed_at', sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('pending', 'running', 'completed', 'failed')", name='valid_forecast_job_status'))
    op.create_index('ix_forecast_jobs_unit_id', 'forecast_jobs', ['unit_id'])


def downgrade():
    op.drop_table('forecast_jobs')
    op.drop_table('forecast_models')
