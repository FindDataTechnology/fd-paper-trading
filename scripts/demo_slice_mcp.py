#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""演示切片：agent 真的能在公网 MCP 上下单成交，并清回干净。

`domain-demo-vertical-slice` 的「真实落点」证据。与 `demo_clickthrough.py`（走 REST）
互补：那条证明「网页能用」，这条证明「**通过 MCP 接口的 agent 能用**」——而 agent 才是
壹座/谦面客户实际的那个调用者。

链路（全部对公网求值，2026-11-27 实测通）：

    账号密码 → 铸 fdt_ token → POST /mcp/ initialize → tools/list
      → tools/call place_order → 断言 FILLED + 成交价 > 0
      → tools/call list_positions → 断言持仓出现
      → POST /api/v1/accounts/{id}/reset → 清回初始态

**token 不落盘**：每次现场用账号密码铸一把，用完即弃。演示账号密码只从环境变量读，
默认值仅用于测试环境；生产化时必须换成 secret 注入。

四态退出码（与 chain-electrification 同口径）：

    0 pass / 1 fail（链断）/ 2 not_configured（缺凭据）/ 3 unreachable（上游不可达）

用法：
    python3 scripts/demo_slice_mcp.py
    python3 scripts/demo_slice_mcp.py --self-test
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("PAPER_BASE_URL", "https://paper.finddatatech.cloud").rstrip("/")
EMAIL = os.environ.get("PAPER_DEMO_EMAIL", "demo-paper-readiness@findmail.com")
PASSWORD = os.environ.get("PAPER_DEMO_PASSWORD", "demo-readiness-2026")
SYMBOL = os.environ.get("PAPER_DEMO_SYMBOL", "000600")
QTY = int(os.environ.get("PAPER_DEMO_QTY", "100"))
#: 服务端 tools/list 的实测条数（fd-paper-trading 4.2.4 = 19）。少于这个数说明工具面塌了。
MIN_TOOLS = int(os.environ.get("PAPER_MIN_TOOLS", "19"))

PASS, FAIL, NOT_CONFIGURED, UNREACHABLE = 0, 1, 2, 3


class StepFailed(Exception):
    pass


class Unreachable(Exception):
    pass


def http(method: str, path: str, body=None, token: str | None = None,
         headers: dict | None = None, timeout: float = 30.0) -> tuple[int, str, dict]:
    """(status, body, response_headers)。头是必须的：MCP 的会话 id 只在头里。"""
    h = {"Accept": "application/json"}
    data = None
    if body is not None:
        h["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    if token:
        h["Authorization"] = "Bearer " + token
    if headers:
        h.update(headers)
    req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - 目标来自显式配置
            return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", "replace")
        if e.code >= 500:
            raise Unreachable(f"{method} {path}: HTTP {e.code}") from e
        return e.code, body_text, dict(e.headers or {})
    except Exception as e:  # noqa: BLE001 - 连不上≠功能坏
        raise Unreachable(f"{method} {path}: {type(e).__name__}: {e}") from e


class Mcp:
    """最小的 MCP streamable-http 客户端（只够跑这条链，不追求通用）。"""

    def __init__(self) -> None:
        self.sid: str | None = None
        self.n = 0

    def _post(self, method: str, params=None) -> dict:
        self.n += 1
        body = {"jsonrpc": "2.0", "id": self.n, "method": method}
        if params is not None:
            body["params"] = params
        hdrs = {"Accept": "application/json, text/event-stream"}
        if self.sid:
            hdrs["Mcp-Session-Id"] = self.sid
        status, raw, resp_headers = http("POST", "/mcp/", body, TOKEN, hdrs)
        # 会话 id 只在响应头里（streamable-http 约定）；首轮 initialize 拿到后每轮带上。
        sid = resp_headers.get("Mcp-Session-Id") or resp_headers.get("mcp-session-id")
        if sid:
            self.sid = sid
        if status != 200:
            raise StepFailed(f"MCP {method} HTTP {status}：{raw[:200]}")
        payload = raw
        m = re.search(r"^data: (.*)$", raw, re.M)
        if m:
            payload = m.group(1)
        try:
            return json.loads(payload)
        except json.JSONDecodeError as e:
            raise StepFailed(f"MCP {method} 返回非 JSON：{raw[:200]}") from e

    def initialize(self) -> dict:
        doc = self._post("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                        "clientInfo": {"name": "ddr-slice", "version": "1"}})
        if "result" not in doc:
            raise StepFailed(f"initialize 无 result：{json.dumps(doc)[:200]}")
        return doc["result"]

    def list_tools(self) -> list[dict]:
        doc = self._post("tools/list", {})
        return (doc.get("result") or {}).get("tools") or []

    def call(self, name: str, args: dict) -> tuple[bool, str, dict | None]:
        doc = self._post("tools/call", {"name": name, "arguments": args})
        res = doc.get("result") or {}
        text = ""
        content = res.get("content") or []
        if content and isinstance(content[0], dict):
            text = content[0].get("text") or ""
        # 顺序很重要：文本字段是服务端给的**原生结果**（账户数组 / 订单对象），
        # 而 structuredContent 是 fastmcp 的包裹层（含 wrap_result 等元数据）。
        # 先读 structuredContent 会拿到包裹对象，然后「没有账户」——链路通却判失败。
        payload = None
        if text:
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = None
        if payload is None and isinstance(res.get("structuredContent"), dict):
            payload = res["structuredContent"]
        return bool(res.get("isError")), text, payload


TOKEN = ""


def mint_token() -> str:
    step("登录 + 铸 fdt_ token")
    status, body, _ = http("POST", "/api/v1/auth/login", {"email": EMAIL, "password": PASSWORD})
    if status != 200:
        raise StepFailed(f"登录失败 HTTP {status}：{body[:200]}")
    jwt = (json.loads(body) or {}).get("access_token")
    if not jwt:
        raise StepFailed("登录响应里没有 access_token")
    status, body, _ = http("POST", "/api/v1/tokens", {"name": "slice-mcp"}, jwt)
    if status not in (200, 201):
        raise StepFailed(f"铸 token 失败 HTTP {status}：{body[:200]}")
    tok = (json.loads(body) or {}).get("token")
    if not tok:
        raise StepFailed("铸 token 响应里没有 token 字段")
    return tok


def pick_account(mcp: Mcp) -> str:
    step("list_accounts via MCP")
    err, text, payload = mcp.call("list_accounts", {})
    if err:
        raise StepFailed(f"list_accounts 报错：{text[:200]}")
    accounts = payload if isinstance(payload, list) else []
    if not accounts:
        raise StepFailed("MCP 上没有账户——演示账号未初始化")
    named = [a for a in accounts if isinstance(a, dict) and a.get("name") == "demo-readiness"]
    return str((named or accounts)[0]["id"])


def run() -> int:
    global TOKEN
    if not BASE or not EMAIL or not PASSWORD:
        print("not_configured：缺 PAPER_BASE_URL / PAPER_DEMO_EMAIL / PAPER_DEMO_PASSWORD",
              file=sys.stderr)
        return NOT_CONFIGURED
    print(f"slice (MCP) → {BASE}")
    reset_ok = False
    try:
        TOKEN = mint_token()
        mcp = Mcp()
        step("MCP initialize")
        mcp.initialize()
        step("tools/list")
        tools = mcp.list_tools()
        names = [t.get("name") for t in tools]
        if len(names) < MIN_TOOLS:
            raise StepFailed(f"工具面塌了：{len(names)} < {MIN_TOOLS}（{names[:8]}）")
        for required in ("place_order", "list_positions", "list_accounts"):
            if required not in names:
                raise StepFailed(f"缺必需工具 {required}；现有 {names}")
        account = pick_account(mcp)

        step(f"tools/call place_order {SYMBOL} × {QTY}")
        err, text, payload = mcp.call("place_order", {
            "account_id": account, "symbol": SYMBOL, "side": "BUY",
            "order_type": "MARKET", "quantity": QTY})
        if err:
            raise StepFailed(f"place_order 报错：{text[:300]}")
        order = (payload or {}).get("order") if isinstance(payload, dict) else None
        if not isinstance(order, dict):
            raise StepFailed(f"place_order 无 order 对象：{(text or '')[:300]}")
        if order.get("status") != "FILLED":
            raise StepFailed(f"订单未成交：status={order.get('status')} reason={order.get('reject_reason')}")
        px = order.get("avg_fill_price")
        if not px or float(px) <= 0:
            raise StepFailed(f"成交价非法：{px!r}——没有真实价格就不算成交")

        step("tools/call list_positions")
        err2, text2, pos = mcp.call("list_positions", {"account_id": account})
        if err2:
            raise StepFailed(f"list_positions 报错：{text2[:200]}")
        rows = pos if isinstance(pos, list) else (pos or {}).get("positions") if isinstance(pos, dict) else []
        if not any(isinstance(p, dict) and p.get("symbol") == SYMBOL for p in (rows or [])):
            raise StepFailed(f"成交后持仓里没有 {SYMBOL}")

        step("复位演示账户")
        status, body, _ = http("POST", f"/api/v1/accounts/{account}/reset", {}, TOKEN)
        reset_ok = status == 200
        if not reset_ok:
            print(f"  ! 复位失败（不影响切片判词）：HTTP {status} {body[:150]}", file=sys.stderr)

    except StepFailed as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return FAIL
    except Unreachable as e:
        print(f"UNREACHABLE: {e}", file=sys.stderr)
        return UNREACHABLE

    print(json.dumps({
        "result": "pass", "transport": "mcp", "account_id": account,
        "order_id": order.get("id"), "status": order.get("status"),
        "avg_fill_price": px, "tools": len(names), "account_reset": reset_ok,
        "url": BASE + "/",
    }, ensure_ascii=False))
    return PASS


# ---------------------------------------------------------------- self-test


def self_test() -> int:
    arms: list[tuple[str, bool]] = []

    def chk(n: str, c: bool) -> None:
        arms.append((n, bool(c)))

    def raises(fn, *a) -> bool:
        try:
            fn(*a)
        except StepFailed:
            return True
        return False

    chk("非 FILLED 被拒", raises(_assert_filled, {"status": "REJECTED"}))
    chk("成交价 0 被拒", raises(_assert_filled, {"status": "FILLED", "avg_fill_price": "0"}))
    chk("正常订单通过", not raises(_assert_filled, {"status": "FILLED", "avg_fill_price": "5.9"}))
    chk("工具数不足被拒", raises(_assert_tools, ["place_order"] * 3))
    chk("缺 place_order 被拒", raises(_assert_tools, ["x"] * MIN_TOOLS))
    # 真实工具面 ≥19；自证要同时满足条数与必备项，所以造足量
    full = ["place_order", "list_positions", "list_accounts"] + [f"t{i}" for i in range(MIN_TOOLS - 3)]
    chk("工具齐全通过", not raises(_assert_tools, full))
    chk("退出码四态互异", sorted({PASS, FAIL, NOT_CONFIGURED, UNREACHABLE}) == [0, 1, 2, 3])
    chk("默认跑公网 https（非 mesh 内网）", BASE.startswith("https://") and "finddatatech.cloud" in BASE)
    chk("token 不来自环境变量（现场铸）", not os.environ.get("PAPER_DEMO_TOKEN"))

    bad = [n for n, ok in arms if not ok]
    for n, ok in arms:
        print(("✓" if ok else "✗"), n)
    print(f"\n{len(arms) - len(bad)}/{len(arms)} 通过")
    return 0 if not bad else 1


def _assert_filled(order: dict) -> None:
    if order.get("status") != "FILLED":
        raise StepFailed("未成交")
    px = order.get("avg_fill_price")
    if not px or float(px) <= 0:
        raise StepFailed("成交价非法")


def _assert_tools(names: list[str]) -> None:
    if len(names) < MIN_TOOLS:
        raise StepFailed(f"工具数 {len(names)} < {MIN_TOOLS}")
    for req in ("place_order", "list_positions", "list_accounts"):
        if req not in names:
            raise StepFailed(f"缺 {req}")


def step(msg: str) -> None:
    print(f"  · {msg}", flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="paper 演示切片（MCP 路径）")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    return self_test() if args.self_test else run()


if __name__ == "__main__":
    sys.exit(main())
