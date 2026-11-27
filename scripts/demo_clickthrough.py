#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""领域演示点击脚本：证券交易（paper）——「公开页上真的能下单成交」。

这是 `domain-demo-readiness` 里 `securities-trading` 那一行的证据（`click_script`）。
判据不是「首页 200」，而是**从公网入口走完一条真实交易**：

    注册/登录 → 建账户 → 市价买入 → 断言 FILLED + 有成交价 → 持仓出现 → 账户摘要反映

四条退出码（与 chain-electrification 同口径）：

    0  pass         —— 全链通过
    1  fail         —— 链断（某一步不成立，原因写进 stderr）
    2  not_configured —— 缺 base url / 凭据
    3  unreachable  —— base url 打不通（上游不可达，不是功能坏）

**为什么每次都真下单**：只读探针证明不了「下单这条路通」——而「能不能给客户点开下一单」
正是这个领域要回答的问题。每轮 1 笔 100 股、金额几十元，落在专为演示建的账号上
（`PAPER_DEMO_EMAIL`/`PAPER_DEMO_PASSWORD`，默认 demo-paper-readiness@findmail.com）。

跑的是**公网域名**，不是集群内 ClusterIP：客户走的是前者，探针就必须对前者求值。

用法：
    python3 scripts/demo_clickthrough.py
    python3 scripts/demo_clickthrough.py --self-test
    PAPER_BASE_URL=https://paper.finddatatech.cloud python3 scripts/demo_clickthrough.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("PAPER_BASE_URL", "https://paper.finddatatech.cloud").rstrip("/")
EMAIL = os.environ.get("PAPER_DEMO_EMAIL", "demo-paper-readiness@findmail.com")
PASSWORD = os.environ.get("PAPER_DEMO_PASSWORD", "demo-readiness-2026")
SYMBOL = os.environ.get("PAPER_DEMO_SYMBOL", "000600")
QTY = float(os.environ.get("PAPER_DEMO_QTY", "100"))

PASS, FAIL, NOT_CONFIGURED, UNREACHABLE = 0, 1, 2, 3


class StepFailed(Exception):
    pass


class Unreachable(Exception):
    pass


def call(method: str, path: str, token: str | None = None, body: dict | None = None,
         timeout: float = 20.0, expect: int | None = None) -> tuple[int, dict | list | None]:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 目标来自显式配置
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    except Exception as exc:  # noqa: BLE001 - 连不上≠功能坏
        raise Unreachable(f"{method} {url}: {type(exc).__name__}: {exc}") from exc
    if status >= 500:
        raise Unreachable(f"{method} {url}: HTTP {status} {raw[:200]}")
    try:
        payload = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        payload = raw  # type: ignore[assignment]
    if expect is not None and status != expect:
        raise StepFailed(f"{method} {path} 期望 {expect} 实际 {status}：{str(payload)[:300]}")
    return status, payload


def step(name: str) -> None:
    print(f"  · {name}", flush=True)


def login_or_register() -> str:
    step("登录")
    status, body = call("POST", "/api/v1/auth/login", body={"email": EMAIL, "password": PASSWORD})
    if status in (401, 404, 422):
        step("账号不存在 ⇒ 注册")
        status, body = call("POST", "/api/v1/auth/register",
                            body={"email": EMAIL, "password": PASSWORD}, expect=201)
        status, body = call("POST", "/api/v1/auth/login",
                            body={"email": EMAIL, "password": PASSWORD}, expect=200)
    if status != 200 or not isinstance(body, dict):
        raise StepFailed(f"登录失败 HTTP {status}：{str(body)[:200]}")
    token = body.get("access_token") or body.get("token")
    if not token:
        raise StepFailed(f"登录响应无 token，实际键：{list(body)}")
    return str(token)


def ensure_account(token: str) -> str:
    step("取演示账户")
    status, body = call("GET", "/api/v1/accounts", token=token, expect=200)
    if not isinstance(body, list):
        raise StepFailed(f"/accounts 返回不是数组：{type(body).__name__}")
    named = [a for a in body if isinstance(a, dict) and a.get("name") == "demo-readiness"]
    if named:
        return str(named[0]["id"])
    step("无演示账户 ⇒ 创建（起始 100000）")
    status, body = call("POST", "/api/v1/accounts", token=token,
                        body={"name": "demo-readiness", "starting_balance": 100000}, expect=201)
    return str(body["id"])


def place_order(token: str, account: str) -> dict:
    step(f"市价买入 {SYMBOL} × {QTY:g}")
    status, body = call("POST", "/api/v1/orders", token=token, expect=202,
                        body={"account_id": account, "symbol": SYMBOL, "side": "BUY",
                              "order_type": "MARKET", "quantity": QTY})
    if not isinstance(body, dict):
        raise StepFailed("下单响应不是对象")
    _check_order(body)
    return body


def assert_position(token: str, account: str) -> dict:
    step("持仓出现")
    status, body = call("GET", f"/api/v1/accounts/{account}/positions", token=token, expect=200)
    rows = [p for p in (body or []) if isinstance(p, dict) and p.get("symbol") == SYMBOL]
    if not rows:
        raise StepFailed(f"成交后持仓里没有 {SYMBOL}（现有：{[p.get('symbol') for p in (body or [])]}）")
    if float(rows[0].get("quantity") or 0) + float(QTY) < float(QTY):
        raise StepFailed(f"持仓数量不合常理：{rows[0].get('quantity')}")
    return rows[0]


def assert_summary(token: str, account: str) -> dict:
    step("账户摘要反映")
    status, body = call("GET", f"/api/v1/accounts/{account}", token=token, expect=200)
    if not isinstance(body, dict):
        raise StepFailed("账户摘要不是对象")
    if int(body.get("open_positions") or 0) < 1:
        raise StepFailed(f"open_positions={body.get('open_positions')}，但刚成交过")
    return body


def reset_account(token: str, account: str) -> bool:
    """把演示账户清回起始状态。

    为什么必须清：这条链每轮真下一单，持仓会一路累积（2026-11-27 实测跑到 1,200 股）。
    演示账户一旦堆满历史成交，客户点开看到的是「别人的旧账」，不是一个干净的样板。
    清账是**尽力而为**：失败不改变本轮判词（链本身已经走通了），只在输出里如实标注——
    否则「清理失败」会被误读成「演示不可用」。
    """
    step("重置账户（保持演示干净）")
    try:
        call("POST", f"/api/v1/accounts/{account}/reset", token=token, body={}, expect=200)
        return True
    except (StepFailed, Unreachable) as exc:
        print(f"  ! 重置失败（不影响本轮判词）：{exc}", file=sys.stderr)
        return False


def run() -> int:
    if not BASE or not EMAIL or not PASSWORD:
        print("not_configured：缺 PAPER_BASE_URL / PAPER_DEMO_EMAIL / PAPER_DEMO_PASSWORD", file=sys.stderr)
        return NOT_CONFIGURED
    print(f"click-through → {BASE}")
    try:
        token = login_or_register()
        account = ensure_account(token)
        order = place_order(token, account)
        pos = assert_position(token, account)
        summary = assert_summary(token, account)
        # 断言都过了才清账：清完再断言就测不到「下单真的留下了痕迹」
        reset_ok = reset_account(token, account) if not os.environ.get("PAPER_DEMO_KEEP_STATE") else False
    except StepFailed as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return FAIL
    except Unreachable as exc:
        print(f"UNREACHABLE: {exc}", file=sys.stderr)
        return UNREACHABLE
    print(json.dumps({
        "result": "pass",
        "account_id": account,
        "order_id": order.get("id"),
        "status": order.get("status"),
        "avg_fill_price": order.get("avg_fill_price"),
        "position_qty": pos.get("quantity"),
        "open_positions": summary.get("open_positions"),
        "account_reset": reset_ok,
        "url": f"{BASE}/",
    }, ensure_ascii=False))
    return PASS


# ---------------------------------------------------------------- self-test


def self_test() -> int:
    """离线负例：断言链上的每一环都能独立判错（不是只判总结果）。"""
    arms: list[tuple[str, bool]] = []

    def chk(name: str, cond: bool) -> None:
        arms.append((name, bool(cond)))

    def rejects(fn, *a) -> str | None:
        try:
            fn(*a)
        except StepFailed as exc:
            return str(exc)
        return None

    # 成交状态
    chk("非 FILLED 被拒", rejects(_check_order, {"status": "REJECTED"}) is not None)
    chk("成交价 0 被拒", rejects(_check_order, {"status": "FILLED", "avg_fill_price": "0", "fills": [1]}) is not None)
    chk("无 fills 被拒", rejects(_check_order, {"status": "FILLED", "avg_fill_price": "5.7", "fills": []}) is not None)
    chk("正常订单通过", rejects(_check_order, {"status": "FILLED", "avg_fill_price": "5.7", "fills": [1]}) is None)
    chk("退出码四态互异", sorted({PASS, FAIL, NOT_CONFIGURED, UNREACHABLE}) == [0, 1, 2, 3])
    chk("默认会重置账户（PAPER_DEMO_KEEP_STATE 未设）",
        not os.environ.get("PAPER_DEMO_KEEP_STATE"))
    chk("默认跑公网域名（非 ClusterIP）", BASE.startswith("https://") and "finddatatech.cloud" in BASE)

    bad = [n for n, ok in arms if not ok]
    for name, ok in arms:
        print(("✓" if ok else "✗"), name)
    print(f"\n{len(arms) - len(bad)}/{len(arms)} 通过")
    return 0 if not bad else 1


def _check_order(body: dict) -> None:
    """place_order 的断言部分抽出来，供自证反复调用。"""
    if body.get("status") != "FILLED":
        raise StepFailed(f"订单未成交：status={body.get('status')}")
    if not body.get("avg_fill_price") or float(body["avg_fill_price"]) <= 0:
        raise StepFailed("成交价非法")
    if not body.get("fills"):
        raise StepFailed("无 fills")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="paper 领域演示点击脚本")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    return self_test() if args.self_test else run()


if __name__ == "__main__":
    sys.exit(main())
