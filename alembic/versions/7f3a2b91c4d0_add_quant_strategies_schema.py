"""add quant strategies schema

Revision ID: 7f3a2b91c4d0
Revises: 990f85bdff01
Create Date: 2026-08-08 12:00:00.000000

Four new tables (``user_groups``, ``strategies``, ``strategy_runs``,
``strategy_equity_points``), ``users.group_id``/``users.is_admin``,
``orders.strategy_run_id`` (indexed), and a seeded ``default`` user group
that all existing users are assigned to.
"""
import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '7f3a2b91c4d0'
down_revision: Union[str, Sequence[str], None] = '990f85bdff01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# JSONB on Postgres, generic JSON on SQLite (tests/dev run migrations too).
JSONB = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

# Fixed id for the seeded group so app code can reference it deterministically.
# Keep in sync with fd_trade.persistence.models.DEFAULT_GROUP_ID.
DEFAULT_GROUP_ID = uuid.UUID("8f3a6b21-0000-4000-8000-000000000001")

DEFAULT_GROUP_CONFIG = {
    "max_active_strategies": 3,
    "max_backtest_days": 730,
    "max_orders_per_day": 20,
    "allow_custom_code": False,
    "allowed_symbols": None,
}


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('user_groups',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('config', JSONB, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('user_groups', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_groups_name'), ['name'], unique=True)

    op.create_table('strategies',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('spec', JSONB, nullable=False),
    sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('strategies', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_strategies_user_id'), ['user_id'], unique=False)

    op.create_table('strategy_runs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('strategy_id', sa.Uuid(), nullable=False),
    sa.Column('strategy_version', sa.Integer(), nullable=False),
    sa.Column('mode', sa.String(length=10), nullable=False),
    sa.Column('account_id', sa.Uuid(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('params', JSONB, nullable=True),
    sa.Column('stats', JSONB, nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('stopped_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['strategy_id'], ['strategies.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('strategy_runs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_strategy_runs_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_strategy_runs_strategy_id'), ['strategy_id'], unique=False)

    op.create_table('strategy_equity_points',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('run_id', sa.Uuid(), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('equity', sa.Numeric(precision=20, scale=4), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['strategy_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'date', name='uq_strategy_equity_points_run_date')
    )
    with op.batch_alter_table('strategy_equity_points', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_strategy_equity_points_run_id'), ['run_id'], unique=False)

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_admin', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column('group_id', sa.Uuid(), nullable=True))
        batch_op.create_foreign_key('fk_users_group_id', 'user_groups', ['group_id'], ['id'], ondelete='SET NULL')

    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('strategy_run_id', sa.Uuid(), nullable=True))
        batch_op.create_foreign_key('fk_orders_strategy_run_id', 'strategy_runs', ['strategy_run_id'], ['id'], ondelete='SET NULL')
        batch_op.create_index(batch_op.f('ix_orders_strategy_run_id'), ['strategy_run_id'], unique=False)

    # Seed the default group and assign all existing users to it.
    user_groups = sa.table(
        'user_groups',
        sa.column('id', sa.Uuid),
        sa.column('name', sa.String),
        sa.column('config', JSONB),
        sa.column('created_at', sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        user_groups,
        [{
            'id': DEFAULT_GROUP_ID,
            'name': 'default',
            'config': DEFAULT_GROUP_CONFIG,
            'created_at': datetime.now(timezone.utc),
        }],
    )
    users = sa.table('users', sa.column('group_id', sa.Uuid))
    op.execute(users.update().values(group_id=DEFAULT_GROUP_ID))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_orders_strategy_run_id'))
        batch_op.drop_constraint('fk_orders_strategy_run_id', type_='foreignkey')
        batch_op.drop_column('strategy_run_id')

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint('fk_users_group_id', type_='foreignkey')
        batch_op.drop_column('group_id')
        batch_op.drop_column('is_admin')

    with op.batch_alter_table('strategy_equity_points', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_strategy_equity_points_run_id'))

    op.drop_table('strategy_equity_points')
    with op.batch_alter_table('strategy_runs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_strategy_runs_strategy_id'))
        batch_op.drop_index(batch_op.f('ix_strategy_runs_status'))

    op.drop_table('strategy_runs')
    with op.batch_alter_table('strategies', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_strategies_user_id'))

    op.drop_table('strategies')
    with op.batch_alter_table('user_groups', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_groups_name'))

    op.drop_table('user_groups')
