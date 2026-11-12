"""线自描述端点 ``/.well-known/line-manifest.json`` 的判据（change converge-line-identity-and-keys 1.4）。

线上现状是**假绿**：该路径回 200，但内容是 421 字节的 ``index.html``——SPA 兜底把它
吃了。所以这里的判据不能只看状态码，必须同时钉住 **content-type 是 JSON** 与
**字节等于仓根清单**；再加一条"另一条不存在的路径仍然回 SPA"，证明只有这一条被精确
放行，而不是把兜底整个拆了。

覆盖的判据（与任务书 1-6 一一对应，另加两条本仓布局特有的钉子）：

1. 成功响应与仓根 ``line-manifest.json`` 逐字节相同；
2. ``content-type`` 含 ``application/json``（排除 SPA 假绿）；
3. ``Cache-Control`` 只在 200 上出现；
4. ``LINE_MANIFEST_PATH`` 生效且即封顶（不再往下猜）；
5. 缺失=404、坏 JSON=500，两者不混；
6. 真 app（starlette ``TestClient``）请求该路径 → 200 且字节相同，**另一条不存在的
   路径仍回 SPA**；
7. （本仓布局）源码树候选用 ``parents[2]`` 命中仓根——``parents[1]`` 是 ``src/``，
   照抄扁平布局模板会永远 404；
8. （装配顺序）well-known 路由在 SPA 兜底之前注册。
"""
from __future__ import annotations

import asyncio
import json
import pathlib

import pytest
from starlette.testclient import TestClient

from fd_paper_trading import wellknown
from fd_paper_trading.api.app import create_app
from fd_paper_trading.marketdata import SimulatedMarketDataProvider

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_MANIFEST = REPO_ROOT / "line-manifest.json"
TEST_DB_URL = "sqlite+aiosqlite://"


@pytest.fixture
def spa_dist(tmp_path):
    """A built-SPA directory: the fallback that used to eat this path."""
    (tmp_path / "index.html").write_text("<html>SPA</html>")
    return tmp_path


@pytest.fixture
def live_client(spa_dist):
    """The real app this repo assembles, with the SPA fallback active.

    ``TestClient`` is used *without* entering it: entering runs the lifespan,
    which starts the strategy supervisor and queries ``strategy_runs`` — the
    endpoint under test needs neither, and these routes touch no DB at all.
    """
    application = create_app(
        database_url=TEST_DB_URL,
        market_data_provider=SimulatedMarketDataProvider(),
        web_dist_dir=spa_dist,
    )
    yield TestClient(application)
    asyncio.run(application.state.db.engine.dispose())

# --- 1. 原样回吐仓根真源 ------------------------------------------------


def test_serves_repo_manifest_byte_for_byte():
    """端点是投影，不是第二真源：字节必须一模一样（不是解析后再 dump）。"""
    assert REPO_MANIFEST.exists(), f"仓根真源不见了：{REPO_MANIFEST}"
    status, body, headers = wellknown.manifest_response()
    assert status == 200
    assert body == REPO_MANIFEST.read_bytes()
    assert json.loads(body)["line"]  # 至少是个带 line 的清单，不是空壳


# --- 2. content-type：排除 SPA 假绿 -------------------------------------


def test_content_type_is_json_not_html(live_client):
    """200 + 421 字节 index.html 也是 200；content-type 才能把假绿照出来。"""
    res = live_client.get(wellknown.WELL_KNOWN_PATH)
    assert res.status_code == 200
    assert "application/json" in res.headers["content-type"]
    assert res.headers["content-type"] == "application/json; charset=utf-8"
    assert res.content == REPO_MANIFEST.read_bytes()
    assert b"<html>" not in res.content


# --- 3. 缓存头只挂 200 --------------------------------------------------


def test_cache_control_only_on_success(live_client, monkeypatch, tmp_path):
    ok = live_client.get(wellknown.WELL_KNOWN_PATH)
    assert ok.status_code == 200
    assert ok.headers["cache-control"] == "public, max-age=300"

    monkeypatch.setenv(wellknown.ENV_MANIFEST_PATH, str(tmp_path / "nope.json"))
    missing = live_client.get(wellknown.WELL_KNOWN_PATH)
    assert missing.status_code == 404
    assert "cache-control" not in missing.headers

    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    monkeypatch.setenv(wellknown.ENV_MANIFEST_PATH, str(bad))
    broken = live_client.get(wellknown.WELL_KNOWN_PATH)
    assert broken.status_code == 500
    assert "cache-control" not in broken.headers


# --- 4. LINE_MANIFEST_PATH 生效且封顶 -----------------------------------


def test_env_override_wins_and_caps_the_chain(tmp_path, monkeypatch):
    """显式指路时不再往下猜：源码树里有真清单也要以覆盖项为准。"""
    only = tmp_path / "only.json"
    only.write_text('{"line":"probe"}')
    monkeypatch.setenv(wellknown.ENV_MANIFEST_PATH, str(only))
    assert wellknown.manifest_candidates() == [only]
    status, body, _ = wellknown.manifest_response()
    assert status == 200 and json.loads(body) == {"line": "probe"}


def test_env_override_is_served_by_the_real_app(live_client, monkeypatch, tmp_path):
    only = tmp_path / "override.json"
    only.write_bytes(b'{"line":"override"}')
    monkeypatch.setenv(wellknown.ENV_MANIFEST_PATH, str(only))
    res = live_client.get(wellknown.WELL_KNOWN_PATH)
    assert res.status_code == 200
    assert res.content == b'{"line":"override"}'
    assert "application/json" in res.headers["content-type"]


# --- 5. 404 / 500 分流 --------------------------------------------------


def test_missing_manifest_is_404_not_500(tmp_path, monkeypatch):
    monkeypatch.setenv(wellknown.ENV_MANIFEST_PATH, str(tmp_path / "nope.json"))
    status, body, headers = wellknown.manifest_response()
    assert status == 404
    assert b"not published" in body
    assert "cache-control" not in headers


def test_invalid_json_is_500_not_404(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    monkeypatch.setenv(wellknown.ENV_MANIFEST_PATH, str(bad))
    status, body, headers = wellknown.manifest_response()
    assert status == 500
    assert b"not valid JSON" in body
    assert "cache-control" not in headers


# --- 6. 真 app 的精确放行 + SPA 边界 ------------------------------------


def test_real_app_serves_this_path_and_spa_still_falls_back(live_client):
    res = live_client.get(wellknown.WELL_KNOWN_PATH)
    assert res.status_code == 200, res.text
    assert res.content == REPO_MANIFEST.read_bytes()
    assert "application/json" in res.headers["content-type"]

    # 兜底没被拆：别的未知路径照旧回 index.html。
    spa = live_client.get("/accounts/123/orders")
    assert spa.status_code == 200
    assert "SPA" in spa.text
    assert "text/html" in spa.headers["content-type"]


def test_wellknown_route_registered_before_spa_fallback(spa_dist):
    """顺序即语义：晚于兜底就永远被 index.html 盖住（线上假绿的成因）。"""
    application = create_app(
        database_url=TEST_DB_URL,
        market_data_provider=SimulatedMarketDataProvider(),
        web_dist_dir=spa_dist,
    )
    paths = [getattr(route, "path", None) for route in application.routes]
    assert wellknown.WELL_KNOWN_PATH in paths
    assert "/{full_path:path}" in paths
    assert paths.index(wellknown.WELL_KNOWN_PATH) < paths.index("/{full_path:path}")
    asyncio.run(application.state.db.engine.dispose())


# --- 7. 本仓 src 布局：源码树候选必须上溯两级 ---------------------------


def test_candidate_chain_finds_repo_manifest_from_any_cwd(tmp_path, monkeypatch):
    """镜像里包在 site-packages、CWD 才是仓根；源码树候选也必须命中仓根。

    ``parents[1]`` 是 ``src/``（src 布局），所以正确的一档是 ``parents[2]``。
    """
    monkeypatch.delenv(wellknown.ENV_MANIFEST_PATH, raising=False)
    monkeypatch.chdir(tmp_path)
    assert wellknown.manifest_path() == REPO_MANIFEST
    assert wellknown.manifest_candidates()[0] == REPO_ROOT / "line-manifest.json"
