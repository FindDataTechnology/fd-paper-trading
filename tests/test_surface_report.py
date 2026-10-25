"""启动自报模块（add-self-reported-tool-surface 2.1/2.2）。

钉住：上报体构造、退避序列、无配置跳过、失败不阻塞启动、/health 三态。
"""

from __future__ import annotations

import asyncio
from typing import ClassVar

import pytest

from fd_paper_trading.mcp import surface_report as sr


class FakeMCP:
    def __init__(self, tools):
        self._tools = tools
        self._wire_surface_report = "not_configured"

    async def get_tools(self):
        return {name: object() for name in self._tools}


class FakeMCP4:
    """fastmcp 4.x 形态：无 get_tools，只有 list_tools（Tool 对象序列）。

    生产镜像实测 4.1.3（2026-10-24）：只认 get_tools 会在 payload 构造期
    AttributeError——一次 HTTP 都发不出，启动自报静默失败。
    """

    class _Tool:
        def __init__(self, name):
            self.name = name

    def __init__(self, tools):
        self._tools = tools
        self._wire_surface_report = "not_configured"

    async def list_tools(self):
        return [self._Tool(n) for n in self._tools]


class FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


class _FakeAsyncClient:
    """The instance report_surface actually uses (httpx.AsyncClient shape)."""

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        FakeClient.posts.append({"url": url, "json": json, "headers": headers})
        rs = FakeClient.responses
        return rs.pop(0) if rs else FakeResp(200, {"num_tools": 1, "updated": True, "reason": "changed"})


class FakeClient:
    """Module stand-in: exposes AsyncClient the way the real httpx module does."""

    AsyncClient = _FakeAsyncClient
    posts: ClassVar[list] = []
    responses: ClassVar[list] = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        type(self).posts.append({"url": url, "json": json, "headers": headers})
        return type(self).responses.pop(0) if type(self).responses else FakeResp(200, {"num_tools": 1, "updated": True, "reason": "changed"})


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("REGISTRY_INTERNAL_URL", "WIRE_SURFACE_TOKEN", "WIRE_SURFACE_ENTRY_PATH",
              "REGISTRY_SURFACE_BACKOFF"):
        monkeypatch.delenv(k, raising=False)


async def test_not_configured_skips_silently(monkeypatch):
    """无配置：report 返回 False、零网络调用——本地形态与现状一致。"""
    result = await sr.report_surface(FakeMCP(["t"]), httpx=FakeClient)
    assert result is False
    assert sr.report_state(False, configured=False) == "not_configured"


async def test_report_payload_and_auth(monkeypatch):
    """上报体：运行时枚举的工具名 + surface_version；Bearer 进 Authorization。"""
    monkeypatch.setenv("REGISTRY_INTERNAL_URL", "https://registry.example")
    monkeypatch.setenv("WIRE_SURFACE_TOKEN", "srf_tok")
    monkeypatch.setenv("WIRE_SURFACE_ENTRY_PATH", "/wire-build-mcp")
    monkeypatch.setenv("REGISTRY_SURFACE_BACKOFF", "0,0,0,0")
    FakeClient.posts = []
    FakeClient.responses = [FakeResp(200, {"num_tools": 2, "updated": True, "reason": "changed"})]

    ok = await sr.report_surface(
        FakeMCP(["wire_artifact_doc_create", "wire_dataset_schema"]), httpx=FakeClient)

    assert ok is True
    post = FakeClient.posts[0]
    assert post["url"].endswith("/api/surface-report/servers/wire-build-mcp/tool-list")
    names = [t["name"] for t in post["json"]["tools"]]
    assert names == ["wire_artifact_doc_create", "wire_dataset_schema"]  # sorted, runtime truth
    assert post["json"]["surface_version"]  # digest present (tool-surface.json ships)
    assert post["headers"]["Authorization"] == "Bearer srf_tok"


async def test_bounded_retry_then_give_up(monkeypatch):
    """500 一路到底：退避 4 次（0 之外）+ 首发共 5 连击，然后放弃、启动不受阻。"""
    monkeypatch.setenv("REGISTRY_INTERNAL_URL", "https://registry.example")
    monkeypatch.setenv("WIRE_SURFACE_TOKEN", "srf_tok")
    monkeypatch.setenv("WIRE_SURFACE_ENTRY_PATH", "/x")
    monkeypatch.setenv("REGISTRY_SURFACE_BACKOFF", "0,0")  # 快进退避
    FakeClient.posts = []
    FakeClient.responses = [FakeResp(500, {"detail": "boom"}) for _ in range(10)]

    ok = await sr.report_surface(FakeMCP(["t"]), httpx=FakeClient)

    assert ok is False
    assert len(FakeClient.posts) == 3  # first + 2 backoff rounds


async def test_retry_succeeds_after_transient(monkeypatch):
    monkeypatch.setenv("REGISTRY_INTERNAL_URL", "https://registry.example")
    monkeypatch.setenv("WIRE_SURFACE_TOKEN", "t")
    monkeypatch.setenv("WIRE_SURFACE_ENTRY_PATH", "/x")
    monkeypatch.setenv("REGISTRY_SURFACE_BACKOFF", "0,0")
    FakeClient.posts = []
    FakeClient.responses = [FakeResp(503, {}), FakeResp(200, {"num_tools": 1})]

    ok = await sr.report_surface(FakeMCP(["t"]), httpx=FakeClient)

    assert ok is True
    assert len(FakeClient.posts) == 2


async def test_fastmcp4_list_tools_shape(monkeypatch):
    """fastmcp 4.x（list_tools、无 get_tools）也要枚举成功并如实上报。

    回归钉：2026-10-24 生产 4.1.3 镜像上启动自报因 AttributeError
    一次都没发出（fire-and-forget 吞成 failed）。
    """
    monkeypatch.setenv("REGISTRY_INTERNAL_URL", "https://registry.example")
    monkeypatch.setenv("WIRE_SURFACE_TOKEN", "srf_tok")
    monkeypatch.setenv("WIRE_SURFACE_ENTRY_PATH", "/wire-build-mcp")
    monkeypatch.setenv("REGISTRY_SURFACE_BACKOFF", "0,0")
    FakeClient.posts = []
    FakeClient.responses = [FakeResp(200, {"num_tools": 2, "updated": True, "reason": "changed"})]

    ok = await sr.report_surface(FakeMCP4(["b_tool", "a_tool"]), httpx=FakeClient)

    assert ok is True
    names = [t["name"] for t in FakeClient.posts[0]["json"]["tools"]]
    assert names == ["a_tool", "b_tool"]


def test_health_state_transitions():
    assert sr.report_state(True, configured=True) == "ok"
    assert sr.report_state(False, configured=True) == "failed"
    assert sr.report_state(False, configured=False) == "not_configured"


async def test_ensure_started_unconfigured_seals():
    mcp = FakeMCP(["t"])
    sr.ensure_started(mcp)
    assert mcp._wire_surface_task is False   # sealed: never retried
    assert sr.start_background.__doc__ is not None


async def test_start_background_marks_state_via_module(monkeypatch):
    """start_background 不注入 fake httpx，直接用真模块——所以测模块接缝：
    patch report_surface，钉「状态被置上、fire-and-forget 不抛」。"""
    monkeypatch.setenv("REGISTRY_INTERNAL_URL", "https://registry.invalid")
    monkeypatch.setenv("WIRE_SURFACE_TOKEN", "t")
    monkeypatch.setenv("WIRE_SURFACE_ENTRY_PATH", "/x")

    async def fake_report(mcp, *, httpx=None):
        return True

    monkeypatch.setattr(sr, "report_surface", fake_report)

    mcp = FakeMCP(["t"])
    task = sr.start_background(mcp)
    assert task is not None
    await asyncio.wait_for(task, 5)
    assert mcp._wire_surface_report == "ok"


async def test_start_background_marks_failed_state(monkeypatch):
    monkeypatch.setenv("REGISTRY_INTERNAL_URL", "https://registry.invalid")
    monkeypatch.setenv("WIRE_SURFACE_TOKEN", "t")
    monkeypatch.setenv("WIRE_SURFACE_ENTRY_PATH", "/x")
    monkeypatch.setenv("REGISTRY_SURFACE_BACKOFF", "0,0,0,0")

    async def failing_report(mcp, *, httpx=None):
        raise RuntimeError("boom inside runner")

    monkeypatch.setattr(sr, "report_surface", failing_report)

    mcp = FakeMCP(["t"])
    task = sr.start_background(mcp)
    assert task is not None
    await asyncio.wait_for(task, 5)
    assert mcp._wire_surface_report == "failed"
