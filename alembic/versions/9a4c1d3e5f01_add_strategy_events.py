"""add strategy_events table

Revision ID: 9a4c1d3e5f01
Revises: 7f3a2b91c4d0
Create Date: 2026-08-09 12:00:00.000000

One new table (``strategy_events``) persisting the live-run timeline so the
web UI can show a run's history after the fact (design D8 / task 7.4).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9a4c1d3e5f01'
down_revision: Union[str, Sequence[str], None] = '7f3a2b91c4d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "strategy_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["strategy_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_strategy_events_run_ts", "strategy_events", ["run_id", "ts"]
    )
    op.create_index("ix_strategy_events_run_id", "strategy_events", ["run_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_strategy_events_run_id", table_name="strategy_events")
    op.drop_index("ix_strategy_events_run_ts", table_name="strategy_events")
    op.drop_table("strategy_events")
