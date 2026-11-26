"""账本落账客户端的判据（spec `paper-run-decision-ledger` 任务 1.1–1.3）。

只用 `httpx.MockTransport` 观测**发出去的请求体与头**，不启真 fd-quant 进程：

1. 请求体：`tools/call` 的 arguments 形状（record_decision 的参数逐字透传）；
2. 头：`X-Fd-Caller` 每个请求都带（验签头是账本认身份的唯一轨）；
3. SSE 帧二次解析：`result.content[0].text` 是字符串化的 JSON；
4. 端点解析三形态（任务 1.2）；
5. 不可达 ⇒ `LedgerUnavailable` 且**不重试**（任务 1.3：initialize 只发一次）；
6. 无身份头 ⇒ 拒发请求（任务 2.2：不发无头请求冒充匿名）。
"""
from __future__ import annotations

import json

import httpx
import pytest

from fd_paper_trading.strategy import ledger_plane
from fd_paper_trading.strategy.ledger_plane import LedgerUnavailable


PAYLOAD = {
    "schema_version": "1",
    "as_of": "2026-11-26",
    "subject": "600519.SS",
    "side": "buy",
    "conviction": None,
    "price_target": None,
    "target_evidence_ids": [],
    "basis": "test basis",
    "evidence": [],
    "producer": {"pack": "fd-paper-trading", "skill": "strategy-runner"},
}

ARGS = {
    "as_of": "2026-11-26",
    "pack": "fd-paper-trading",
    "skill": "strategy-runner",
    "payload": PAYLOAD,
    "record_kind": "single",
    "symbol": "600519.SS",
    "caller_header": "u@x.io.1764000000.deadbeef",
}


def _mock(calls: list[dict], *, result_text='{"created": true}') -> httpx.MockTransport:
    """记录每次请求（method + 头 + body），按 call_tool 的期望顺序作答。"""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content or b"{}")
        calls.append({
            "method": payload.get("method"),
            "headers": dict(request.headers),
            "params": payload.get("params"),
        })
        if payload.get("method") == "initialize":
            return httpx.Response(
                200,
                headers={"mcp-session-id": "sid-1", "content-type": "application/json"},
                json={"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}},
            )
        if payload.get("method") == "notifications/initialized":
            return httpx.Response(202)
        body = (
            'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"content":'
            '[{"type":"text","text":' + json.dumps(json.dumps(json.loads(result_text))) + '}]}}\n\n'
        )
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)

    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (ledger_plane.LEDGER_ENDPOINT_ENV, ledger_plane.COMPUTE_ENDPOINT_ENV):
        monkeypatch.delenv(var, raising=False)
    yield


def test_request_body_and_caller_header_are_correct():
    """请求体逐字透传参数，验签头三个请求都带（任务 1.1 / 2.1）。"""
    calls: list[dict] = []
    out = ledger_plane.record_decision(**ARGS, base_url="http://quant.local/mcp",
                                       transport=_mock(calls))
    assert out == {"created": True}
    assert [c["method"] for c in calls] == [
        "initialize", "notifications/initialized", "tools/call",
    ]
    tool_call = calls[-1]
    assert tool_call["params"]["name"] == "record_decision"
    sent = tool_call["params"]["arguments"]
    assert sent["as_of"] == "2026-11-26"
    assert sent["pack"] == "fd-paper-trading"
    assert sent["skill"] == "strategy-runner"
    assert sent["payload"] == PAYLOAD
    assert sent["record_kind"] == "single"
    assert sent["symbol"] == "600519.SS"
    for c in calls:
        assert c["headers"].get("x-fd-caller") == ARGS["caller_header"], c


def test_sse_second_parse_returns_tool_result():
    """SSE 帧里的 text 是字符串化 JSON，须二次解析成 dict（任务 1.1）。"""
    calls: list[dict] = []
    out = ledger_plane.record_decision(
        **ARGS, base_url="http://q.local/mcp",
        transport=_mock(calls, result_text='{"created": true, "decision_id": "dec-1"}'),
    )
    assert out == {"created": True, "decision_id": "dec-1"}


def test_portfolio_args_pass_through():
    """组合形态：portfolio_key / target_weights / series_ref 逐字透传。"""
    calls: list[dict] = []
    tw = {"schema_version": "1", "as_of": "2026-11-26",
          "universe": ["600000", "600036"],
          "weights": {"600000": 0.5, "600036": 0.5},
          "cash_floor": 0.0,
          "producer": {"pack": "fd-paper-trading", "skill": "strategy-runner"}}
    ref = {"source_id": "s", "symbol": "600000", "adjustment": "qfq",
           "as_of_max": "2026-11-26", "fingerprint": "f"}
    ledger_plane.record_decision(
        as_of="2026-11-26", pack="fd-paper-trading", skill="strategy-runner",
        payload={"schema_version": "1", "as_of": "2026-11-26"},
        record_kind="portfolio", portfolio_key="strategy_run:r1",
        target_weights=tw, series_ref=ref,
        caller_header=ARGS["caller_header"],
        base_url="http://q.local/mcp", transport=_mock(calls),
    )
    sent = calls[-1]["params"]["arguments"]
    assert sent["record_kind"] == "portfolio"
    assert sent["portfolio_key"] == "strategy_run:r1"
    assert "symbol" not in sent
    assert sent["target_weights"] == tw
    assert sent["series_ref"] == ref


def test_missing_caller_header_refuses_to_send():
    """无身份头 ⇒ 拒发请求（任务 2.2）：不发无头请求冒充匿名落账。"""
    calls: list[dict] = []
    with pytest.raises(LedgerUnavailable) as exc:
        ledger_plane.record_decision(
            **{**ARGS, "caller_header": None},
            base_url="http://q.local/mcp", transport=_mock(calls),
        )
    assert exc.value.reason == "no_identity_key"
    assert calls == [], "无头请求根本不该发出去"


def test_endpoint_resolution_three_forms(monkeypatch):
    """任务 1.2：显式 env → 计算面 env 的 base+/mcp → 都缺 ⇒ 抛未配置。"""
    monkeypatch.setenv(ledger_plane.LEDGER_ENDPOINT_ENV, "http://ledger:9000/mcp")
    assert ledger_plane.endpoint() == "http://ledger:9000/mcp"
    monkeypatch.delenv(ledger_plane.LEDGER_ENDPOINT_ENV)
    monkeypatch.setenv(ledger_plane.COMPUTE_ENDPOINT_ENV, "http://fd-quant:8080/mcp")
    assert ledger_plane.endpoint() == "http://fd-quant:8080/mcp"
    monkeypatch.setenv(ledger_plane.COMPUTE_ENDPOINT_ENV, "http://fd-quant:8080")
    assert ledger_plane.endpoint() == "http://fd-quant:8080/mcp"
    monkeypatch.delenv(ledger_plane.COMPUTE_ENDPOINT_ENV)
    with pytest.raises(LedgerUnavailable) as exc:
        ledger_plane.endpoint()
    assert exc.value.reason == "endpoint_not_configured"


def test_unreachable_fails_without_retry():
    """不可达 ⇒ LedgerUnavailable，且 initialize 只发一次（任务 1.3：不重试风暴）。"""
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content or b"{}").get("method"))
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(LedgerUnavailable) as exc:
        ledger_plane.record_decision(
            **ARGS, base_url="http://dead.local/mcp",
            transport=httpx.MockTransport(handler),
        )
    assert exc.value.reason == "ledger_unreachable"
    assert calls.count("initialize") == 1, "单次尝试，不重试"


def test_ledger_rejection_is_classified_as_rejected():
    """账本显式拒绝（工具报错）≠ 环境故障：原因码分开说。"""
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content or b"{}")
        if payload.get("method") == "initialize":
            return httpx.Response(
                200, headers={"mcp-session-id": "sid-1"},
                json={"jsonrpc": "2.0", "id": 1, "result": {}})
        if payload.get("method") == "notifications/initialized":
            return httpx.Response(202)
        body = ('event: message\ndata: {"jsonrpc":"2.0","id":2,"error":'
                '{"code":-32000,"message":"payload disagrees"}}\n\n')
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)

    with pytest.raises(LedgerUnavailable) as exc:
        ledger_plane.record_decision(
            **ARGS, base_url="http://q.local/mcp", transport=httpx.MockTransport(handler),
        )
    assert exc.value.reason == "ledger_rejected"
