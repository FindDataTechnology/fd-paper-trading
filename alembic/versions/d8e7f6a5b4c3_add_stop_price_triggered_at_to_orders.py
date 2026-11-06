"""add stop_price and triggered_at to orders for STOP/STOP_LIMIT orders

Revision ID: d3e8f1a2b4c5
Revises: c5f3d9b21a07
Create Date: 2026-11-06 15:00:00.000000

Add ``stop_price`` and ``triggered_at`` columns to ``orders`` table to support
STOP and STOP_LIMIT order types. Both nullable with no default: existing rows
keep NULL (no stop price / not triggered), which is the honest shape of
"order placed before this feature existed".
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd8e7f6a5b4c3'
down_revision: Union[str, Sequence[str], None] = 'c5f3d9b21a07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add stop_price and triggered_at columns to orders table."""
    op.add_column(
        "orders",
        sa.Column("stop_price", sa.Numeric(20, 8), nullable=True)
    )
    op.add_column(
        "orders",
        sa.Column(
            "triggered_at",
            sa.DateTime(timezone=True),
            nullable=True
        )
    )


def downgrade() -> None:
    """Drop the STOP order columns."""
    op.drop_column("orders", "triggered_at")
    op.drop_column("orders", "stop_price")