"""order_intents: 纸面下单的意图票

Revision ID: a7c1e9d4b620
Revises: f2b3c4d5e6a7
Create Date: 2026-11-11 11:40:00.000000

Why a separate table (see ``OrderIntentRecord``): a ticket records the DECISION
(which weight period, which gate verdict), an order records the ACTION. They do
not share a lifecycle — one rebalance can produce several tickets, and a ticket
can have no order at all (refused, or cut by the daily cap). Keeping them apart
means any "per-order" query is not silently diluted by ticket rows.

``intent`` stores the contract instance verbatim, the same way the decision
ledger stores payloads: a later contract change still reads back the shape that
was actually decided under.

JSONB on Postgres, JSON on SQLite — the model declares ``JSONB`` and the ORM
maps it per dialect; the migration uses the plain ``sa.JSON`` type so it applies
to both without a dialect branch.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a7c1e9d4b620'
down_revision: Union[str, Sequence[str], None] = 'f2b3c4d5e6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "order_intents",
        sa.Column("intent_id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "run_id", sa.Uuid(),
            sa.ForeignKey("strategy_runs.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "account_id", sa.Uuid(),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=False),
        sa.Column("order_type", sa.String(8), nullable=False, server_default="market"),
        # 契约实例逐字快照（derived_from / risk_verdict / paper_only 都在里面）
        sa.Column("intent", sa.JSON(), nullable=False),
        # 记录侧判断：按契约这笔票导不导得出去（不是契约字段）
        sa.Column("exportable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column(
            "order_id", sa.Uuid(),
            sa.ForeignKey("orders.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_order_intents_run_id", "order_intents", ["run_id"])
    op.create_index("ix_order_intents_account_id", "order_intents", ["account_id"])
    op.create_index("ix_order_intents_as_of", "order_intents", ["as_of"])
    op.create_index("ix_order_intents_symbol", "order_intents", ["symbol"])
    op.create_index("ix_order_intents_order_id", "order_intents", ["order_id"])
    op.create_index("ix_order_intents_created_at", "order_intents", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_order_intents_created_at", table_name="order_intents")
    op.drop_index("ix_order_intents_order_id", table_name="order_intents")
    op.drop_index("ix_order_intents_symbol", table_name="order_intents")
    op.drop_index("ix_order_intents_as_of", table_name="order_intents")
    op.drop_index("ix_order_intents_account_id", table_name="order_intents")
    op.drop_index("ix_order_intents_run_id", table_name="order_intents")
    op.drop_table("order_intents")
