"""make (account_id, client_order_id) unique on orders

Revision ID: e1a2b3c4d5f6
Revises: d8e7f6a5b4c3
Create Date: 2026-11-08 10:00:00.000000

``client_order_id`` was indexed but not unique, so the same submission key could
create two orders and move cash twice. The unique constraint makes the key an
actual idempotency key; NULLs stay multi-allowed (a unique index treats NULLs as
distinct), so manually-placed orders without a client id are unaffected.

The pre-existing plain index (``ix_orders_client_order_id``) is dropped because
the unique constraint's index covers the same lookups.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e1a2b3c4d5f6'
down_revision: Union[str, Sequence[str], None] = 'd8e7f6a5b4c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the (account_id, client_order_id) unique constraint.

    batch_alter_table: SQLite cannot ALTER constraints in place (it raises
    "No support for ALTER of constraints in SQLite dialect"); batch mode does
    the copy-and-move. Postgres takes the plain ALTER path.
    """
    with op.batch_alter_table("orders") as batch_op:
        batch_op.create_unique_constraint(
            "uq_orders_account_client_order_id",
            ["account_id", "client_order_id"],
        )
        batch_op.drop_index("ix_orders_client_order_id")


def downgrade() -> None:
    """Drop the unique constraint, restore the plain index."""
    with op.batch_alter_table("orders") as batch_op:
        batch_op.create_index(
            "ix_orders_client_order_id", ["client_order_id"], unique=False
        )
        batch_op.drop_constraint(
            "uq_orders_account_client_order_id", type_="unique"
        )
