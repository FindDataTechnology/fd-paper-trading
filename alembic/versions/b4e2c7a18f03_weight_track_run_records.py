"""weight-track run records

Revision ID: b4e2c7a18f03
Revises: 9a4c1d3e5f01
Create Date: 2026-10-17 06:00:00.000000

Three nullable columns on ``strategy_runs`` for the weight track:
``universe`` (pinned at run start), ``weight_snapshots`` (per-rebalance target,
actual and instruction detail), ``data_gaps`` (dates a universe member was valued
from a stale close). Nullable with no backfill because no existing run is a
weight run: a null here means "single-symbol run", which is a fact about the
row, not missing data.

Also seeds ``max_universe_size`` into the ``default`` group's config so the new
limit is visible in the group row rather than only in the code default.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b4e2c7a18f03'
down_revision: Union[str, Sequence[str], None] = '9a4c1d3e5f01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# JSONB on Postgres, generic JSON on SQLite (tests/dev run migrations too).
JSONB = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

# Keep in sync with fd_paper_trading.persistence.models.DEFAULT_GROUP_ID.
DEFAULT_GROUP_ID = "8f3a6b21-0000-4000-8000-000000000001"


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('strategy_runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('universe', JSONB, nullable=True))
        batch_op.add_column(sa.Column('weight_snapshots', JSONB, nullable=True))
        batch_op.add_column(sa.Column('data_gaps', JSONB, nullable=True))

    # Seed the new limit only where the key is absent: a group that already set its
    # own value (or deliberately set null) must not be overwritten by a migration.
    conn = op.get_bind()
    groups = sa.table(
        'user_groups',
        sa.column('id', sa.Uuid()),
        sa.column('config', JSONB),
    )
    rows = conn.execute(sa.select(groups.c.id, groups.c.config)).fetchall()
    for group_id, config in rows:
        merged = dict(config or {})
        if "max_universe_size" not in merged:
            merged["max_universe_size"] = 20
            conn.execute(
                groups.update().where(groups.c.id == group_id).values(config=merged)
            )


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    groups = sa.table(
        'user_groups',
        sa.column('id', sa.Uuid()),
        sa.column('config', JSONB),
    )
    rows = conn.execute(sa.select(groups.c.id, groups.c.config)).fetchall()
    for group_id, config in rows:
        merged = dict(config or {})
        if merged.pop("max_universe_size", None) is not None:
            conn.execute(
                groups.update().where(groups.c.id == group_id).values(config=merged)
            )

    with op.batch_alter_table('strategy_runs', schema=None) as batch_op:
        batch_op.drop_column('data_gaps')
        batch_op.drop_column('weight_snapshots')
        batch_op.drop_column('universe')
