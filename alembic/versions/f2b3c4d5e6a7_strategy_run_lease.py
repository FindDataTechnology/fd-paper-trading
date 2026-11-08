"""single-writer lease on strategy_runs

Revision ID: f2b3c4d5e6a7
Revises: e1a2b3c4d5f6
Create Date: 2026-11-08 16:30:00.000000

A paper run must be driven by one process at a time. ``replicas: 1`` with the
default RollingUpdate still starts a second pod before the first dies, and both
``recover()`` the same runs: both poll the same bars and both place the orders,
because the "already processed" guard (``params["last_bar_date"]``) is written at
the END of a tick, after the orders went in. A deploy on a trading day therefore
duplicates every signal in flight.

The lease is two nullable columns, claimed by a conditional UPDATE, so it works
on SQLite (tests) and Postgres identically and needs no advisory-lock dialect
branch. Both columns are nullable with no default: an old row reads as "no lease
held", which is the correct shape for a run that predates this and is not being
driven by any pod.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f2b3c4d5e6a7'
down_revision: Union[str, Sequence[str], None] = 'e1a2b3c4d5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("strategy_runs") as batch_op:
        batch_op.add_column(sa.Column("runner_owner", sa.String(64), nullable=True))
        batch_op.add_column(
            sa.Column("runner_lease_until", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("strategy_runs") as batch_op:
        batch_op.drop_column("runner_lease_until")
        batch_op.drop_column("runner_owner")
