"""fills 费用三列 + position_lots 表（T+1 批次）

Revision ID: a1b2c3d4e5f6
Revises: c3e5a7b9d1f2
Create Date: 2026-11-24 12:00:00.000000

两件事，同属 change paper-fees-and-t1-settlement：

1. ``fills`` 加 ``commission`` / ``stamp_tax`` / ``slippage_cost``，**可空、无默认**。
   NULL ＝ 本列上线前的成交（从未记录过费用），0 ＝ 记录了、确实是零。把历史行填 0
   等于声称它们零费用，而它们按当时的零成本规则成交——那是真的没扣费，但不是"费用
   为零"这个事实，两者在报告上必须可区分。与 ``fills.realized_pnl`` 同一处置理由。

2. ``position_lots``：一笔买入一个批次，带开仓日与含费单位成本。T+1 的"今天买的不
   能今天卖"与 FIFO 已实现盈亏都需要"哪批股"，而均价把这件事抹平了。
   ``remaining`` 存值不重算：``reset`` 清批次但历史成交行还在，重算会复活已清空的批次。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'c3e5a7b9d1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("fills") as batch_op:
        batch_op.add_column(sa.Column("commission", sa.Numeric(20, 8), nullable=True))
        batch_op.add_column(sa.Column("stamp_tax", sa.Numeric(20, 8), nullable=True))
        batch_op.add_column(sa.Column("slippage_cost", sa.Numeric(20, 8), nullable=True))

    op.create_table(
        "position_lots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("account_id", sa.Uuid(),
                  sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("instrument_id", sa.String(80), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("opened_on", sa.Date(), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=False),
        sa.Column("remaining", sa.Numeric(20, 8), nullable=False),
        sa.Column("unit_cost", sa.Numeric(20, 8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_position_lots_account_id", "position_lots", ["account_id"])
    op.create_index(
        "ix_position_lots_account_instrument_opened",
        "position_lots",
        ["account_id", "instrument_id", "opened_on"],
    )


def downgrade() -> None:
    op.drop_index("ix_position_lots_account_instrument_opened", table_name="position_lots")
    op.drop_index("ix_position_lots_account_id", table_name="position_lots")
    op.drop_table("position_lots")
    with op.batch_alter_table("fills") as batch_op:
        batch_op.drop_column("slippage_cost")
        batch_op.drop_column("stamp_tax")
        batch_op.drop_column("commission")
