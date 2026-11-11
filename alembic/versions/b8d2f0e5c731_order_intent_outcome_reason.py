"""order_intents: 未成交原因

Revision ID: b8d2f0e5c731
Revises: a7c1e9d4b620
Create Date: 2026-11-11 13:40:00.000000

The spec requires every intent to reconcile against "filled / refused / not
triggered" **with the reason** for the non-filled cases. ``blocked_reason``
answers a different question (does the risk contract allow this ticket out at
all), so the two live in separate columns rather than one overloaded field.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'b8d2f0e5c731'
down_revision: Union[str, Sequence[str], None] = 'a7c1e9d4b620'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("order_intents", sa.Column("outcome_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("order_intents") as batch_op:
        batch_op.drop_column("outcome_reason")
