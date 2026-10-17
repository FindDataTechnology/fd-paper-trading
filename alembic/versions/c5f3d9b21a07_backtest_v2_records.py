"""backtest engine v2: cost / benchmark / walk-forward / scan records

Revision ID: c5f3d9b21a07
Revises: b4e2c7a18f03
Create Date: 2026-10-17 12:00:00.000000

Six nullable columns on ``strategy_runs`` (design D4/D5/D6, spec "Backtest
statistics and equity curve" + walk-forward + parameter scan):

- ``cost_config`` / ``cost_summary`` — the effective cost parameters and the
  aggregated commission/tax/slippage. Snapshotted per run because "the same
  strategy scored differently" is first explained by whether costs changed.
- ``benchmark_config`` / ``benchmark_results`` — the requested benchmarks and
  each one's curve + relative metrics (or its explicit unavailability reason).
- ``walkforward_windows`` — per-window train/test dates and stats.
- ``param_grid`` / ``scan_results`` / ``in_sample`` — the scan request, its
  result matrix, and the machine-readable "this is in-sample" marker.

All nullable with no server default: existing rows keep NULL, which is the
honest representation of "this run predates these capabilities" (and is what
makes the backward-compatibility test meaningful rather than vacuous).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5f3d9b21a07'
down_revision: Union[str, Sequence[str], None] = 'b4e2c7a18f03'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_COLUMNS = (
    "cost_config",
    "cost_summary",
    "benchmark_config",
    "benchmark_results",
    "walkforward_windows",
    "param_grid",
    "scan_results",
)


def upgrade() -> None:
    """Add the v2 record columns; all nullable, no defaults.

    ``in_sample`` is deliberately NOT in ``_NEW_COLUMNS``: the model declares it
    ``Boolean`` (the machine-readable in-sample marker is a flag, not a JSON
    payload), and PostgreSQL enforces the declared type — writing a Python bool
    into a JSON column raises DatatypeMismatchError on real inserts (SQLite in
    tests is typeless and hides exactly this drift, which is how it shipped).
    It gets its own Boolean column below.
    """
    for name in _NEW_COLUMNS:
        op.add_column("strategy_runs", sa.Column(name, sa.JSON(), nullable=True))
    op.add_column("strategy_runs", sa.Column("in_sample", sa.Boolean(), nullable=True))


def downgrade() -> None:
    """Drop the v2 record columns (existing data in them is discarded)."""
    op.drop_column("strategy_runs", "in_sample")
    for name in reversed(_NEW_COLUMNS):
        op.drop_column("strategy_runs", name)
