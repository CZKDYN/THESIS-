"""Account lifecycle and retirement of retained demonstration units.

Revision ID: b782410cdf65
Revises: a13f260be924
"""
from alembic import op
import sqlalchemy as sa

revision = 'b782410cdf65'
down_revision = 'a13f260be924'
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    op.add_column('electricity_rates', sa.Column('is_demo', sa.Boolean(), nullable=False, server_default=sa.false()))
    connection.execute(sa.text("UPDATE electricity_rates SET is_demo = true WHERE created_by IS NULL AND EXISTS (SELECT id FROM audit_logs WHERE action = 'demo_seeded')"))
    constraint = next(item for item in sa.inspect(connection).get_unique_constraints('users')
                      if item['column_names'] == ['unit_id'])
    with op.batch_alter_table('users', naming_convention={'uq': 'uq_%(table_name)s_%(column_0_name)s'}) as batch:
        batch.drop_constraint(constraint['name'] or 'uq_users_unit_id', type_='unique')
        batch.add_column(sa.Column('tenancy_ended_at', sa.DateTime(timezone=True)))
        batch.add_column(sa.Column('must_change_password', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column('session_version', sa.Integer(), nullable=False, server_default='1'))
    condition = sa.text("role = 'tenant' AND tenancy_ended_at IS NULL")
    op.create_index('uq_current_tenant_unit', 'users', ['unit_id'], unique=True,
                    postgresql_where=condition, sqlite_where=condition)
    # Preserve history and money. Synthetic accounts cannot become occupied units.
    connection.execute(sa.text('UPDATE dormitory_units SET archived_at = COALESCE(archived_at, CURRENT_TIMESTAMP) WHERE is_demo = true'))
    connection.execute(sa.text("UPDATE users SET active = false, session_version = session_version + 1 WHERE role = 'tenant' AND unit_id IN (SELECT id FROM dormitory_units WHERE is_demo = true)"))
    connection.execute(sa.text("UPDATE load_requests SET status = 'rejected', resolved_at = CURRENT_TIMESTAMP WHERE status = 'pending' AND unit_id IN (SELECT id FROM dormitory_units WHERE is_demo = true)"))
    connection.execute(sa.text("UPDATE forecast_jobs SET status = 'failed', completed_at = CURRENT_TIMESTAMP, message = 'Demonstration unit retired; register a hardware unit for real use.' WHERE status IN ('pending', 'running') AND unit_id IN (SELECT id FROM dormitory_units WHERE is_demo = true)"))
    connection.execute(sa.text("INSERT INTO audit_logs (action, detail, created_at) VALUES ('real_mode_migration', 'Demonstration units archived; historical readings, credits and model artifacts retained.', CURRENT_TIMESTAMP)"))


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM (SELECT unit_id FROM users WHERE unit_id IS NOT NULL GROUP BY unit_id HAVING count(*) > 1) AS duplicates')):
        raise RuntimeError('Cannot downgrade while retained former tenants share a unit. Preserve their history.')
    op.drop_index('uq_current_tenant_unit', table_name='users')
    op.drop_column('electricity_rates', 'is_demo')
    with op.batch_alter_table('users') as batch:
        batch.drop_column('session_version')
        batch.drop_column('must_change_password')
        batch.drop_column('tenancy_ended_at')
        batch.create_unique_constraint('uq_users_unit_id', ['unit_id'])
