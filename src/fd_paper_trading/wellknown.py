"""线自描述端点 ``/.well-known/line-manifest.json``（change converge-line-identity-and-keys 1.4）。

家族实现：公开、免鉴权、零网络。**原样回吐**本仓 ``line-manifest.json``——仓根那份是
唯一真源，这个端点只是它的投影；一旦在这里加工（重新 dump、补字段），就出现第二个真源，
两处投影必然漂移。

为什么路径是「候选链」而不是一条固定的：本仓是 **src 布局**，``src/fd_paper_trading/``
才是包目录，镜像里 ``uv sync`` 又把包装进 site-packages，所以 ``__file__`` 与仓根之间
隔着 ``src/``（源码树）或整个 venv（镜像）。本仓库布局下 ``parents[1]`` 指向 ``src/``
而不是仓根——**必须用 ``parents[2]``**（``parents[0]``=fd_paper_trading,
``parents[1]``=src, ``parents[2]``=仓根）。镜像里真正的那份在 ``WORKDIR /app``
（Dockerfile ``COPY line-manifest.json ./``），由 ``Path.cwd()`` 一档命中。
``LINE_MANIFEST_PATH`` 显式指路时即封顶，不再往下猜。

两个状态码分得很清：**缺失=404**（该线未发布清单，属配置事实）；**存在但不可解析=500**
（属缺陷，不能伪装成 404 让调用方以为"本来就没有"）。

本仓入站**没有**共享 bearer 中间件（鉴权是各路由的 FastAPI dependency），所以这里
不需要家族里那种 ``EXEMPT_PATHS`` 配套改动；若将来加了全局门，必须把
``WELL_KNOWN_PATH`` 一并豁免。

**注册顺序**：``register()`` 必须在 SPA 兜底路由（``/{full_path:path}``）**之前**调用。
FastAPI 按注册顺序匹配，晚于兜底就永远被 ``index.html`` 盖住——线上 200/421 字节的
假绿正是这么来的。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

#: 与家族其它线（wire-ops-mcp / facet-ops-mcp / fd-info）逐字相同的路径常量。
WELL_KNOWN_PATH = "/.well-known/line-manifest.json"
ENV_MANIFEST_PATH = "LINE_MANIFEST_PATH"
#: 清单是运行期契约：改一次要生效，但不能让边缘缓存长期撒谎。
CACHE_CONTROL = "public, max-age=300"

_CONTENT_TYPE = "application/json; charset=utf-8"


def manifest_candidates() -> list[Path]:
    """按优先级列出可能放清单的位置（``LINE_MANIFEST_PATH`` 一项即封顶）。

    注意 ``parents[2]``：本仓是 src 布局，文件位于 ``src/fd_paper_trading/wellknown.py``，
    ``parents[1]`` 是 ``src/``，仓根再往上一级。模板仓（扁平布局）用的是 ``parents[1]``，
    直接照抄会永远找不到仓根清单。
    """
    override = os.environ.get(ENV_MANIFEST_PATH)
    if override:
        return [Path(override)]
    here = Path(__file__).resolve()
    return [
        here.parents[2] / "line-manifest.json",  # 源码树：src/fd_paper_trading/ → 仓根
        Path.cwd() / "line-manifest.json",       # 镜像：WORKDIR /app 里 COPY 的那份
        here.parent / "line-manifest.json",      # 清单与包同级（兜底）
    ]


def manifest_path() -> Path:
    """实际取用的清单路径；都不存在时返回首选位置（让 404 能点名期望路径）。"""
    candidates = manifest_candidates()
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def manifest_response() -> tuple[int, bytes, dict[str, str]]:
    """(status, body, headers) —— 不读网络、不建会话。

    成功时附 ``Cache-Control``，失败时不附：别把"暂时没发布"的 404 缓存成长期事实。
    """
    path = manifest_path()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return 404, b'{"detail":"line-manifest not published"}', {}
    except OSError as exc:
        detail = f"line-manifest unreadable: {type(exc).__name__}"
        return 500, json.dumps({"detail": detail}).encode(), {}
    try:
        json.loads(raw)
    except ValueError:
        return 500, b'{"detail":"line-manifest is not valid JSON"}', {}
    return 200, raw, {"cache-control": CACHE_CONTROL}


def register(app) -> None:
    """把端点挂到 FastAPI 应用上。

    用 ``app.get`` 而不是中间件：路由属于应用本身，天然只放行**精确这一条路径**，
    其余未知路径继续落到 SPA 兜底（判据 6 钉住的就是这个边界）。

    调用点必须在 SPA 兜底之前（见模块 docstring）。
    """
    from starlette.responses import Response

    @app.get(WELL_KNOWN_PATH, include_in_schema=False)
    async def line_manifest() -> Response:  # noqa: ANN202 - ASGI 边界
        status, body, headers = manifest_response()
        return Response(
            content=body, status_code=status, media_type=_CONTENT_TYPE, headers=headers
        )
