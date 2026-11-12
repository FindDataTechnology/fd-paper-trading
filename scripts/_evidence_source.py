"""证据脚本共用的取源器：走注册表解析，别手搓 provider。

为什么要有这个文件（2026-11-12 实测）：`AstockMarketDataProvider` 的
``source`` 已改成**必填**（钉住联邦视图的 ``source_used``，见 change
`add-price-supply-and-caliber`），而两个证据脚本仍然 `AstockMarketDataProvider(dsn=dsn)`，
于是**拿到 DSN 也会先抛 ValueError**——"缺凭据"和"脚本已腐烂"是两件事，
后者不修的话，凭据一到手仍然跑不出证据，还会被误读成"数据有问题"。

解析顺序与生产完全一致（`marketdata.selection.resolve_selection`）：
`MARKET_DATA_SOURCE_ID` → 注册表声明 → ``access.dsn_env`` 取 DSN。
本机 `MARKET_DATA_SOURCE_ID` 为空时解析结果是 ``simulated``，这里**直接拒绝**：
证据必须来自真源，"模拟源跑通"不是证据。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))


class EvidenceSourceError(RuntimeError):
    """证据取源不可用（缺注册表 id / 缺 DSN / 解析成模拟源）。"""


def open_provider():
    """按注册表解析出一个**真源** provider；任何不满足都抛可执行的错误。

    只认注册表声明的凭据槽（当前三条源都声明 ``ASTOCK_DSN``）。不给别名：
    ``FD_OPEN_DATA_DSN`` 之类的旁路曾让"到底该配哪个变量"变成猜谜，
    手上若是别的名字，``export ASTOCK_DSN="$FD_OPEN_DATA_DSN"`` 一行即可。
    """
    from fd_paper_trading.config import get_settings
    from fd_paper_trading.marketdata.astock import AstockMarketDataProvider
    from fd_paper_trading.marketdata.selection import (
        PROVIDER_FEDERATED,
        SourceSelectionError,
        resolve_selection,
    )

    settings = get_settings()
    try:
        sel = resolve_selection(settings)
    except SourceSelectionError as e:
        raise EvidenceSourceError(str(e)) from e

    if sel.provider != PROVIDER_FEDERATED:
        raise EvidenceSourceError(
            f"解析到 provider={sel.provider!r}（source_id={sel.source_id!r}）——"
            "证据必须走真源 federated_view。设 MARKET_DATA_SOURCE_ID="
            "astock_daily（或 akshare / index-seed）后重试；空值会解析成 simulated。"
        )

    # 凭据槽的检查**不在这里**：`resolve_selection` 已经按注册表声明逐条校验
    # （缺 `access.dsn_env` 时它自己就抛 SourceSelectionError，上面统一转成
    # EvidenceSourceError，消息里点名具体变量）。这里原先还有一段 `if not dsn: raise`
    # ——注入对照证明它**永不执行**，是死代码，删掉，别留两道看起来都像在守同一件事的门。
    return AstockMarketDataProvider(
        dsn=sel.dsn or "",
        source=sel.db_source,
        calendar_refs=sel.calendar_refs or "",
        registry_source_id=sel.source_id,
    )
