"""fd-quant 决策账本的落账客户端（design D1/D2，spec `paper-run-decision-ledger`）。

## 为什么复用 `compute_plane.call_tool` 而不是再写一份握手

MCP streamable HTTP 的协议细节（initialize → Mcp-Session-Id → SSE 帧二次
json.loads）计算面客户端已经解决并实测过；再抄一份就是第二份协议实现，两份必然
漂移。本模块只负责账本特有的三件事：

1. **端点解析**（任务 1.2）：`FD_QUANT_LEDGER_ENDPOINT` → 缺省回落
   `FD_QUANT_COMPUTE_ENDPOINT` 的 base + `/mcp`（账本与计算面是同一个 fd-quant
   服务，两个 env 指向同一台）；两者皆缺 ⇒ 抛"未配置"，**不静默跳过**——静默
   跳过会让"闭环断了"伪装成"没有决策"。
2. **调用者身份头**（design D2）：账本按 caller 隔离存储，身份由验签头解析。
   头由 `ledger_identity.caller_header` 现签；本模块只负责带上它——**没有头
   就不发请求**（无头请求会被解析成 anonymous，等于冒充匿名落账，任务 2.2）。
3. **单次尝试、快速失败**（任务 1.3）：单请求超时 5s，没有任何重试。失败抛
   `LedgerUnavailable`，由 runner 计为落账失败并留痕——**不阻断交易**（design
   D4：账本是留痕，不是交易的一部分）。

与计算面的纪律差异，正是本模块存在的理由：计算面不可用 ⇒ 回测 FAILED（口径
一致性优先）；账本不可用 ⇒ 计数失败、事件留痕、交易照常。
"""
from __future__ import annotations

import os
from typing import Any, Mapping, Optional

from . import compute_plane

#: 账本端点。与计算面同一个 fd-quant 服务；不配则回落计算面端点的 base + /mcp。
LEDGER_ENDPOINT_ENV = "FD_QUANT_LEDGER_ENDPOINT"
COMPUTE_ENDPOINT_ENV = compute_plane.DEFAULT_ENDPOINT_ENV

#: 单请求超时（任务 1.3）：≤5s，超时即弃，不重试。
LEDGER_TIMEOUT = 5.0

#: 验签头名。与 fd-quant `fd_quant.caller.CALLER_HEADER` 同值——两侧契约的一半。
CALLER_HEADER = "X-Fd-Caller"

#: 稳定原因码（与 fd-quant 的 REASON_* 同风格）：调用方按它分流与留痕。
REASON_NO_KEY = "no_identity_key"
REASON_NO_ENDPOINT = "endpoint_not_configured"
REASON_UNREACHABLE = "ledger_unreachable"
REASON_REJECTED = "ledger_rejected"


class LedgerUnavailable(RuntimeError):
    """账本不可达、拒绝调用或调用前提缺失（未配端点/未配密钥）。

    `reason` 是稳定字符串（REASON_*），调用方按它留痕与分流——重命名异常类
    不该改变对外原因码。
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        super().__init__(f"{reason}: {detail}" if detail else reason)


def endpoint() -> str:
    """账本 MCP 端点：显式 env → 计算面端点的 base + /mcp → 抛"未配置"。

    回落按 **base** 拼接（剥掉已有的 `/mcp` 路径再加回），这样两种配置形态
    （`http://fd-quant:8080` 或 `http://fd-quant:8080/mcp`）落到同一端点。
    """
    direct = (os.environ.get(LEDGER_ENDPOINT_ENV) or "").strip()
    if direct:
        return direct
    base = (os.environ.get(COMPUTE_ENDPOINT_ENV) or "").strip()
    if base:
        root = base.rstrip("/")
        if root.endswith("/mcp"):
            root = root[: -len("/mcp")]
        return root + "/mcp"
    raise LedgerUnavailable(
        REASON_NO_ENDPOINT,
        "set FD_QUANT_LEDGER_ENDPOINT (or FD_QUANT_COMPUTE_ENDPOINT) to the "
        "fd-quant MCP endpoint",
    )


def record_decision(
    *,
    as_of: str,
    pack: str,
    skill: str,
    payload: Mapping[str, Any],
    symbol: Optional[str] = None,
    portfolio_key: Optional[str] = None,
    record_kind: str = "single",
    target_weights: Optional[Mapping[str, Any]] = None,
    series_ref: Optional[Mapping[str, Any]] = None,
    caller_header: Optional[str] = None,
    timeout: float = LEDGER_TIMEOUT,
    base_url: Optional[str] = None,
    transport: Any = None,
) -> dict:
    """调 fd-quant 的 `record_decision`，返回其结果 dict。

    `caller_header` 是**必带**的验签头（缺失即抛，不发无头请求——无头会被
    账本解析成 anonymous，那是以错误身份落库）。`transport` 供测试注入
    `httpx.MockTransport`，使"正常/不可达/拒绝"三条路径都能在无网环境被测到。
    """
    if not (caller_header or "").strip():
        raise LedgerUnavailable(
            REASON_NO_KEY,
            "no signed caller header; refusing to record as anonymous",
        )
    args: dict[str, Any] = {
        "as_of": as_of, "pack": pack, "skill": skill,
        "payload": dict(payload), "record_kind": record_kind,
    }
    if symbol:
        args["symbol"] = symbol
    if portfolio_key:
        args["portfolio_key"] = portfolio_key
    if target_weights is not None:
        args["target_weights"] = dict(target_weights)
    if series_ref is not None:
        args["series_ref"] = dict(series_ref)
    url = base_url or endpoint()
    try:
        return compute_plane.call_tool(
            "record_decision", args, base_url=url, timeout=timeout,
            transport=transport, extra_headers={CALLER_HEADER: caller_header},
        )
    except LedgerUnavailable:
        raise
    except compute_plane.ComputePlaneUnavailable as exc:
        # HTTP 层失败（连接/超时/HTTP 状态/协议断裂）与账本显式拒绝分开说：
        # 前者是环境故障（修复靠运维），后者是这次提交本身的问题（修复靠调用方）。
        # compute_plane 的异常文本即分类依据（同一模块生成，前缀稳定）。
        text = str(exc)
        transport_markers = ("unreachable", "HTTP", "session id", "SSE", "malformed")
        reason = (REASON_UNREACHABLE if any(m in text for m in transport_markers)
                  else REASON_REJECTED)
        raise LedgerUnavailable(reason, text) from exc
