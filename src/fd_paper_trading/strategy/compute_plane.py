"""fd-quant 计算面的 MCP HTTP 客户端（design D2：调用，不复制实现）。

## 为什么是"调用"而不是"本仓实现一份"

跨仓对拍是 spec 级验收项（"与计算面口径一致 … 以跨仓对拍测试证明一致"）。
两份实现——哪怕同源复制——必然漂移，而"回测/账本/研报三处口径不一致"是这类
系统最隐蔽的错。故组合统计与窗口切分一律问计算面要数。

## 为什么不可用时直接失败，不降级

design D2 的风险条目写明了取舍：**口径一致性优先于可用性**。若计算面不可用时
回退到内部实现，同一个用户在不同时刻会得到两套口径的统计，而报告上看不出差别
——那比"跑不了"危险得多。故本模块只抛 `ComputePlaneUnavailable`，由回测把它
转成运行 FAILED 并把原因写进 `run.error`。

## 协议细节（2024-11-05 streamable HTTP，已实测）

1. `POST /mcp` `initialize` → 响应头带 `Mcp-Session-Id`，body 是 SSE 帧
   （`event: message\ndata: {...}`）。
2. `POST /mcp` `notifications/initialized`（无 id，无响应体）。
3. `POST /mcp` `tools/call` → 同样 SSE 帧；`result.content[0].text` 是**字符串化的
   JSON**（工具返回值本身），需二次 `json.loads`。

会话用后即弃（每次调用开一个新会话）：计算面是无状态纯函数服务，复用会话只
换来"会话过期"这一类失败模式，不换来任何东西。
"""
from __future__ import annotations

import json
import os
from typing import Any, Mapping, Optional, Sequence

import httpx

#: 计算面地址。默认指向本机开发端口；生产按部署填 `http://fd-quant-compute:PORT/mcp`。
DEFAULT_ENDPOINT_ENV = "FD_QUANT_COMPUTE_ENDPOINT"
DEFAULT_ENDPOINT = "http://127.0.0.1:8799/mcp"

DEFAULT_TIMEOUT = 15.0

PROTOCOL_VERSION = "2024-11-05"


class ComputePlaneUnavailable(RuntimeError):
    """计算面不可达或拒绝调用。

    单独成类型而不是复用 `EvaluationError`：调用方（回测）需要把"计算面不可用"
    与"策略自己算错了"区分开——前者是环境故障，后者是用户代码问题，两者的
    处置与措辞都不同。
    """


def endpoint() -> str:
    return os.environ.get(DEFAULT_ENDPOINT_ENV, DEFAULT_ENDPOINT)


def _parse_sse(text: str) -> dict:
    """从 SSE 帧里取 JSON-RPC 响应体。

    逐行找 `data:` 前缀并拼接——一个响应可能跨多行 data。忽略 `event:` 行
    （只有 `message` 一种）与心跳注释。
    """
    payloads: list[str] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            payloads.append(line[5:].lstrip())
    if not payloads:
        raise ComputePlaneUnavailable(
            f"compute plane returned no SSE data frame (body head: {text[:200]!r})"
        )
    try:
        return json.loads("\n".join(payloads))
    except json.JSONDecodeError as exc:
        raise ComputePlaneUnavailable(
            f"compute plane returned malformed JSON: {exc}; body head: {text[:200]!r}"
        ) from exc


def call_tool(
    name: str,
    arguments: Mapping[str, Any],
    *,
    base_url: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
    transport: Optional[httpx.BaseTransport] = None,
) -> dict:
    """调用计算面一个工具，返回其 dict 结果。

    `transport` 供测试注入（`httpx.MockTransport`），使"计算面正常/不可用/返回
    错误"三条路径都能在无网络环境下被测到——否则这类错误分支只能靠线上事故发现。
    """
    url = base_url or endpoint()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    try:
        # trust_env=False：不让 httpx 读取环境/系统代理配置。计算面是**内网**
        # 服务（本机开发端口或集群内 service 名），走代理必然失败——而失败形态
        # 是 502/超时这类"看起来像服务没起"的错误，实测误导过一次（本机
        # 8799 未启动时也被代理答了 502，使人误判为服务崩了）。
        with httpx.Client(timeout=timeout, transport=transport, trust_env=False) as client:
            r = client.post(url, json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                           "clientInfo": {"name": "fd-paper-trading", "version": "1"}},
            }, headers=headers)
            if r.status_code != 200:
                raise ComputePlaneUnavailable(
                    f"compute plane initialize returned HTTP {r.status_code} at {url}"
                )
            sid = r.headers.get("mcp-session-id")
            if not sid:
                raise ComputePlaneUnavailable(
                    f"compute plane did not return a session id at {url}"
                )
            session_headers = {**headers, "Mcp-Session-Id": sid}

            client.post(url, json={"jsonrpc": "2.0",
                                   "method": "notifications/initialized",
                                   "params": {}}, headers=session_headers)

            r2 = client.post(url, json={
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": name, "arguments": dict(arguments)},
            }, headers=session_headers)
    except ComputePlaneUnavailable:
        raise
    except (httpx.HTTPError, OSError) as exc:
        raise ComputePlaneUnavailable(
            f"compute plane unreachable at {url} ({type(exc).__name__}: {exc})"
        ) from exc

    if r2.status_code != 200:
        raise ComputePlaneUnavailable(
            f"compute plane tools/call '{name}' returned HTTP {r2.status_code}"
        )
    body = _parse_sse(r2.text)
    if "error" in body:
        err = body["error"]
        raise ComputePlaneUnavailable(
            f"compute plane rejected '{name}': {err.get('message', err)}"
        )
    result = body.get("result") or {}
    if result.get("isError"):
        raise ComputePlaneUnavailable(
            f"compute plane tool '{name}' reported an error: {result}"
        )
    content = result.get("content") or []
    if not content or "text" not in content[0]:
        raise ComputePlaneUnavailable(
            f"compute plane tool '{name}' returned no text content: {result}"
        )
    try:
        return json.loads(content[0]["text"])
    except json.JSONDecodeError as exc:
        raise ComputePlaneUnavailable(
            f"compute plane tool '{name}' returned non-JSON text: {exc}"
        ) from exc


# ── 组合统计的语义封装 ───────────────────────────────────────────
#
# 这些薄封装只做一件事：把回测内部的 Decimal 权益曲线转成计算面要的
# `(dates, values)` 形态。任何"算法"都不在这里——否则就又在本地复制了一份口径。


def _series(dates: Sequence[str], values: Sequence[Any]) -> dict:
    return {"dates": list(dates), "values": [float(v) for v in values]}


def summarize_return(dates, values, as_of, **kw) -> dict:
    return call_tool("summarize_return", {**_series(dates, values), "as_of": as_of, **kw})


def summarize_volatility(dates, values, as_of, **kw) -> dict:
    return call_tool("summarize_volatility", {**_series(dates, values), "as_of": as_of, **kw})


def summarize_drawdown(dates, values, as_of) -> dict:
    return call_tool("summarize_drawdown", {**_series(dates, values), "as_of": as_of})


def summarize_sortino(dates, values, as_of, **kw) -> dict:
    return call_tool("summarize_sortino", {**_series(dates, values), "as_of": as_of, **kw})


def summarize_calmar(dates, values, as_of, **kw) -> dict:
    return call_tool("summarize_calmar", {**_series(dates, values), "as_of": as_of, **kw})


def summarize_information_ratio(dates, values, benchmark, as_of, **kw) -> dict:
    return call_tool("summarize_information_ratio", {
        **_series(dates, values), "benchmark": [float(b) for b in benchmark],
        "as_of": as_of, **kw,
    })


def split_walk_forward_windows(dates, values, as_of, **kw) -> dict:
    return call_tool("split_walk_forward_windows", {
        **_series(dates, values), "as_of": as_of, **kw,
    })


def validate_no_lookahead(dates, as_of, **kw) -> dict:
    return call_tool("validate_no_lookahead", {
        "dates": list(dates), "values": None, "as_of": as_of, **kw,
    })
