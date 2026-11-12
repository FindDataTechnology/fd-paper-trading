"""计算面共享 bearer 的判据（change converge-line-identity-and-keys 8.6）。

fd-quant 侧开了共享门之后，本线的回测计算面必须带同一把令牌。这里只用
`httpx.MockTransport` 观测**发出去的请求头**，不启真进程：

1. 未配 `FD_QUANT_COMPUTE_TOKEN` ⇒ 不带 Authorization（与接入前逐字节一致）；
2. 配了 ⇒ `Authorization: Bearer <token>`，且 `initialize` 与后续两个请求都带
   （第二/第三个请求用 session 头拼装，最容易在实现里漏掉）；
3. 空白令牌视同未配——别让一个空 secret 变成一个 `Bearer ` 空头。

选 MockTransport 而不是起真计算面：这条判据的对象是**我们发的头**，真进程
能证明"通了"却证明不了"带了哪个头"，方向是反的。
"""
from __future__ import annotations

import json

import httpx
import pytest

from fd_paper_trading.strategy import compute_plane


def _mock(records: list[dict]) -> httpx.MockTransport:
    """记录每次请求的头，并按 call_tool 的期望顺序作答。"""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content or b"{}")
        records.append({"method": payload.get("method"), "headers": dict(request.headers)})
        if payload.get("method") == "initialize":
            return httpx.Response(
                200,
                headers={"mcp-session-id": "sid-1", "content-type": "application/json"},
                json={"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}},
            )
        if payload.get("method") == "notifications/initialized":
            return httpx.Response(202)
        # tools/call：SSE 帧
        body = 'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"{\\"ok\\":true}"}]}}\n\n'
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)

    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _clean_token(monkeypatch):
    monkeypatch.delenv(compute_plane.TOKEN_ENV, raising=False)
    yield


def test_no_token_sends_no_authorization(monkeypatch):
    """未配令牌 = 不发这个头：门没开时行为与接入前逐字节一致。"""
    records: list[dict] = []
    compute_plane.call_tool(
        "compute_ma", {"closes": [1, 2, 3]},
        base_url="http://quant.local/mcp", transport=_mock(records),
    )
    assert records, "应当至少发出 initialize"
    for rec in records:
        assert "authorization" not in {k.lower() for k in rec["headers"]}, rec


def test_token_is_sent_on_every_request(monkeypatch):
    """配了令牌 ⇒ 三个请求都带（第二个/第三个用的是拼出来的 session 头）。"""
    monkeypatch.setenv(compute_plane.TOKEN_ENV, "lmc_test_token")
    records: list[dict] = []
    compute_plane.call_tool(
        "compute_ma", {"closes": [1, 2, 3]},
        base_url="http://quant.local/mcp", transport=_mock(records),
    )
    methods = [r["method"] for r in records]
    assert methods[:1] == ["initialize"], methods
    assert len(records) == 3, methods
    for rec in records:
        assert rec["headers"].get("authorization") == "Bearer lmc_test_token", rec


def test_blank_token_is_treated_as_unset(monkeypatch):
    """空串/空白不算配置——否则会发出一个空的 `Bearer ` 头被对方判非法。"""
    monkeypatch.setenv(compute_plane.TOKEN_ENV, "   ")
    assert compute_plane.endpoint_token() == ""
    records: list[dict] = []
    compute_plane.call_tool(
        "compute_ma", {"closes": [1]},
        base_url="http://quant.local/mcp", transport=_mock(records),
    )
    for rec in records:
        assert "authorization" not in {k.lower() for k in rec["headers"]}, rec


def test_401_is_reported_as_unavailable(monkeypatch):
    """开闸后令牌不匹配的表现：401 必须落成 ComputePlaneUnavailable（如实拒绝），
    而不是被当成"策略算错"或静默降级。"""
    monkeypatch.setenv(compute_plane.TOKEN_ENV, "wrong")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    with pytest.raises(compute_plane.ComputePlaneUnavailable) as exc:
        compute_plane.call_tool(
            "compute_ma", {"closes": [1]},
            base_url="http://quant.local/mcp", transport=httpx.MockTransport(handler),
        )
    assert "401" in str(exc.value)
