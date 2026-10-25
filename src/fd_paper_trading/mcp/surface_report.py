"""启动时向 registry 自报工具面（add-self-reported-tool-surface 组 2）。

逐用户凭据服务的 HTTP 层匿名一律 401，registry 探针永远探不到——工具面变更后
快照停旧值，发布门把合法新工具名判 unresolved。本模块在服务启动、工具注册完成
后，把**运行时枚举**的工具名清单报给 registry 的内部自报路由（path 绑定 token）。

纪律（design D5 / 任务 2.1）：
- 失败**不阻塞启动**——registry 不可达时服务照常提供能力；
- 有界重试 15s/1m/5m/30m ×4（env 可配），成功即停——不是心跳；
- 无 `REGISTRY_INTERNAL_URL` 或 `WIRE_SURFACE_TOKEN` 配置时**静默跳过**
  （本地开发/未上牌形态，行为与现状逐字节一致）。
"""

from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

#: 退避序列（秒）。REGISTRY_SURFACE_BACKOFF 可整体覆盖（逗号分隔）。
_DEFAULT_BACKOFF = (15, 60, 300, 1800)


def _backoff_sequence() -> tuple[float, ...]:
    raw = os.environ.get("REGISTRY_SURFACE_BACKOFF")
    if not raw:
        return _DEFAULT_BACKOFF
    try:
        values = tuple(float(x) for x in raw.split(",") if x.strip())
        return values or _DEFAULT_BACKOFF
    except ValueError:
        logger.warning("REGISTRY_SURFACE_BACKOFF unparsable; using default")
        return _DEFAULT_BACKOFF


def _configured() -> tuple[str, str, str] | None:
    """(internal_url, entry_path, token) 或 None（未配置 → 静默跳过）。"""
    base = os.environ.get("REGISTRY_INTERNAL_URL", "").rstrip("/")
    token = os.environ.get("WIRE_SURFACE_TOKEN", "")
    path = os.environ.get("WIRE_SURFACE_ENTRY_PATH", "").strip()
    if not base or not token or not path:
        return None
    if not path.startswith("/"):
        path = "/" + path
    return base, path.rstrip("/"), token


async def _tool_names(mcp) -> list[str]:
    """运行时枚举：真源是注册进 FastMCP 的工具，不是 tool-surface.json。

    `fastmcp>=2.0` 的版本区间横跨两代枚举 API：4.x 只有
    ``list_tools()``（Tool 对象/字典序列），更高版本另提供
    ``get_tools()``（{name: tool} 映射）。按可用性分派——只认其中一个
    API 会在另一代的镜像里 AttributeError（2026-10-24 生产实证：
    启动自报因此一次都没发出去，fire-and-forget 边界吞成 failed）。
    """
    get_tools = getattr(mcp, "get_tools", None)
    if get_tools is not None:
        return sorted((await get_tools()).keys())
    items = await mcp.list_tools()
    return sorted(t["name"] if isinstance(t, dict) else t.name for t in items)


def _surface_version() -> str | None:
    """tool-surface.json 的 sha 摘要（存在才带）——把自报与构建期清单对上版。

    wheel 里它随包走（package-data）；editable/源码树里它在仓库根，
    importlib.resources 找不到——回落 <pkg_dir>/../tool-surface.json。
    """
    try:
        import hashlib
        from pathlib import Path

        here = Path(__file__).resolve().parent
        for cand in (here, *here.parents[:4]):
            if (cand / "tool-surface.json").is_file():
                return hashlib.sha256((cand / "tool-surface.json").read_bytes()).hexdigest()[:12]
    except Exception as exc:  # noqa: BLE001 — 版本标注是可选元数据
        logger.warning("surface_version unavailable: %s", exc)
    return None


async def report_surface(mcp, *, httpx=None) -> bool:
    """报一次；成功 True。所有失败都被吞掉并记日志——启动不受阻。"""
    cfg = _configured()
    if cfg is None:
        logger.info("surface report: not configured (REGISTRY_INTERNAL_URL / token), skipping")
        return False
    base, path, token = cfg

    if httpx is None:
        import httpx as httpx_mod
        httpx = httpx_mod

    payload = {
        "tools": [{"name": name} for name in await _tool_names(mcp)],
        "surface_version": _surface_version(),
    }
    url = f"{base}/api/surface-report/servers{path}/tool-list"

    for attempt, delay in enumerate((0.0,) + _backoff_sequence(), start=1):
        if delay:
            await asyncio.sleep(delay)
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    url, json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                )
            if resp.status_code == 200:
                body = resp.json()
                logger.info(
                    "surface report: ok (%s tool(s), updated=%s reason=%s)",
                    body.get("num_tools"), body.get("updated"), body.get("reason"),
                )
                return True
            logger.warning(
                "surface report: HTTP %s (attempt %s) — %s",
                resp.status_code, attempt, resp.text[:160],
            )
        except Exception as exc:  # noqa: BLE001 — 网络层任何异常都不上抛
            logger.warning(
                "surface report: %s: %s (attempt %s)",
                type(exc).__name__, str(exc)[:120], attempt,
            )
    logger.error("surface report: giving up after %s attempts", len(_backoff_sequence()) + 1)
    return False


def report_state(ok: bool, configured: bool) -> str:
    """/health 的 last_surface_report 口径（任务 2.2）。"""
    if not configured:
        return "not_configured"
    return "ok" if ok else "failed"


def start_background(mcp) -> asyncio.Task | None:
    """fire-and-forget 自报任务；未配置时返回 None（零行为变化）。"""
    if _configured() is None:
        return None

    async def _runner():
        # 吞掉一切异常：网络层失败 report_surface 自己消化，但代码层缺陷
        # （参数错、序列化炸）也不能把后台任务变成未处理异常——状态置 failed。
        try:
            ok = await report_surface(mcp)
            mcp._wire_surface_report = report_state(ok, configured=True)
        except Exception:  # noqa: BLE001 — fire-and-forget 边界
            mcp._wire_surface_report = report_state(False, configured=True)

    return asyncio.create_task(_runner())


def ensure_started(mcp) -> None:
    """首请求触发点：此时事件循环已在跑；只触发一次；未配置置 False 封口。"""
    if getattr(mcp, "_wire_surface_task", None) is not None:
        return
    mcp._wire_surface_task = start_background(mcp) or False
