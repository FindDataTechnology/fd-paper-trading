"""fills.realized_pnl: 逐笔已实现盈亏

Revision ID: c3e5a7b9d1f2
Revises: b8d2f0e5c731
Create Date: 2026-11-14 08:10:00.000000

"今日已实现盈亏"必须能按时间窗口求和，而 `positions.realized_pnl` 是**终身**累计值
（`position.realized_pnl += realized`）——用它在日界两侧做差需要重放成本基础，
那会引入第二套成本口径。加一列记住每一笔成交自己实现的那部分，求和各取所需。

**可空，且 NULL 有语义**：加列时不带默认值，于是升级前已存在的成交全部为 `NULL`
＝"从未记录过归属"。这与 `0`（记录了、确实是零）必须分开——把历史行填 0 等于声称
它们都是保本平仓，而它们可能实现过大额盈亏。回填只能靠重放成本基础（第二套口径），
刻意不做；`GET /accounts/{id}/daily-pnl` 统计窗口内的 `NULL` 笔数并在
``unattributed_fills`` 里报出，不编一个数（spec: daily-pnl-tracking 日界那条末句）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'c3e5a7b9d1f2'
down_revision: Union[str, Sequence[str], None] = 'b8d2f0e5c731'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "fills",
        sa.Column("realized_pnl", sa.Numeric(20, 8), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("fills") as batch_op:
        batch_op.drop_column("realized_pnl")
