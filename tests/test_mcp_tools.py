"""Round-trip tests for the strategy MCP tools against a live REST server.

The tools in ``fd_paper_trading.mcp.server`` call the REST API with ``requests``, so
these tests boot the app under a real uvicorn in a background thread and
exercise the tools end-to-end (strategy CRUD, backtest lifecycle, errors).
"""

import asyncio
import os
import socket
import threading
import time

import pytest
import requests

os.environ.setdefault("PAPER_TRADING_API_TOKEN", "placeholder")  # import-time guard
# The dev shell may export http_proxy; loopback must not go through it.
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
os.environ["no_proxy"] = "127.0.0.1,localhost"

from fd_paper_trading.api.app import create_app  # noqa: E402
from fd_paper_trading.mcp import server as mcp_server  # noqa: E402
from fd_paper_trading.persistence.base import init_db  # noqa: E402

TEMPLATE_SPEC = {
    "kind": "template",
    "template": "sma_cross",
    "symbol": "AAPL",
    "params": {"fast": 2, "slow": 3, "quantity": 10},
}
DCA_SPEC = {
    "kind": "template",
    "template": "dca",
    "symbol": "MSFT",
    "params": {"interval_bars": 2, "quantity": 1},
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    """The app served by uvicorn in a daemon thread, with its own sqlite DB."""
    import uvicorn

    db_path = tmp_path_factory.mktemp("mcp") / "test.db"
    app = create_app(database_url=f"sqlite+aiosqlite:///{db_path}")
    port = _free_port()
    ready = threading.Event()

    def serve() -> None:
        async def main() -> None:
            await init_db(app.state.db)
            config = uvicorn.Config(
                app, host="127.0.0.1", port=port, log_level="error"
            )
            server = uvicorn.Server(config)
            task = asyncio.create_task(server.serve())
            ready.set()
            await task

        asyncio.run(main())

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(timeout=10)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if requests.get(f"{base}/health", timeout=1).status_code == 200:
                break
        except requests.RequestException:
            time.sleep(0.05)
    else:
        raise RuntimeError("live server did not come up")
    return base


@pytest.fixture(scope="module")
def mcp(live_server):
    """The mcp.server module pointed at the live server as a fresh user."""
    r = requests.post(
        f"{live_server}/api/v1/auth/register",
        json={"email": "mcp@example.com", "password": "password123"},
        timeout=5,
    )
    assert r.status_code in (200, 201, 400), r.text
    r = requests.post(
        f"{live_server}/api/v1/auth/login",
        json={"email": "mcp@example.com", "password": "password123"},
        timeout=5,
    )
    token = r.json()["access_token"]
    mcp_server.PAPER_TRADING_API_URL = live_server
    mcp_server.PAPER_TRADING_API_TOKEN = token
    return mcp_server


def test_list_strategy_templates(mcp):
    templates = mcp_server.list_strategy_templates()
    ids = {t["id"] for t in templates}
    assert {"sma_cross", "rsi_reversion", "bollinger_breakout",
            "macd_cross", "dca"} <= ids


def test_create_get_update_list_strategy(mcp):
    created = mcp_server.create_strategy(
        name="mcp strat", spec=TEMPLATE_SPEC, description="via mcp"
    )
    assert created["success"] is True
    strategy = created["strategy"]
    assert strategy["version"] == 1
    assert strategy["spec_summary"].startswith("sma_cross(")

    got = mcp_server.get_strategy(strategy["id"])
    assert got["name"] == "mcp strat"

    # Metadata-only update: no version bump.
    updated = mcp_server.update_strategy(strategy["id"], name="renamed")
    assert updated["strategy"]["version"] == 1
    assert updated["strategy"]["name"] == "renamed"

    # Spec update bumps the version.
    new_spec = {**TEMPLATE_SPEC, "params": {"fast": 3, "slow": 4, "quantity": 5}}
    updated = mcp_server.update_strategy(strategy["id"], spec=new_spec)
    assert updated["strategy"]["version"] == 2

    names = {s["name"] for s in mcp_server.list_strategies()}
    assert "renamed" in names


def test_create_strategy_validation_errors_are_structured(mcp):
    result = mcp_server.create_strategy(
        name="bad",
        spec={"kind": "template", "template": "nope", "symbol": "AAPL", "params": {}},
    )
    assert result["error"] is True
    assert result["code"] == "invalid_spec"
    assert any("unknown template" in f["message"] for f in result["fields"])


def test_run_backtest_and_poll_to_completed(mcp):
    from datetime import date, timedelta

    created = mcp_server.create_strategy(name="bt strat", spec=DCA_SPEC)
    sid = created["strategy"]["id"]
    end = date.today()
    start = end - timedelta(days=20)

    started = mcp_server.run_backtest(sid, start.isoformat(), end.isoformat())
    assert started["success"] is True
    run = started["run"]
    assert run["status"] == "QUEUED"
    assert run["mode"] == "BACKTEST"

    for _ in range(200):
        detail = mcp_server.get_strategy_run(run["id"])
        if detail["status"] in ("COMPLETED", "FAILED"):
            break
        time.sleep(0.05)
    assert detail["status"] == "COMPLETED", detail.get("error")
    assert detail["stats"]["trade_count"] >= 1
    assert len(detail["equity_points"]) >= 1

    runs = mcp_server.list_strategy_runs(strategy_id=sid, status="COMPLETED")
    assert [r["id"] for r in runs] == [run["id"]]
    assert mcp_server.list_strategy_runs(status="FAILED") == []


def test_start_stop_strategy_round_trip(mcp):
    """Positive start -> poll to RUNNING -> stop -> STOPPED against a live server."""
    created = mcp_server.create_strategy(name="live strat", spec=DCA_SPEC)
    sid = created["strategy"]["id"]
    accounts = mcp_server.list_accounts()
    if not accounts:
        # The fresh user has no account yet; create one via the REST API.
        r = requests.post(
            f"{mcp_server.PAPER_TRADING_API_URL}/api/v1/accounts",
            json={"name": "paper"},
            headers={"Authorization": f"Bearer {mcp_server.PAPER_TRADING_API_TOKEN}"},
            timeout=5,
        )
        assert r.status_code in (200, 201), r.text
        accounts = mcp_server.list_accounts()
    account_id = accounts[0]["id"]

    result = mcp_server.start_strategy(sid, account_id)
    assert result["success"] is True
    run_id = result["run"]["id"]
    assert result["run"]["mode"] == "PAPER"
    assert result["run"]["status"] in ("STARTING", "RUNNING")

    # The auto-task ticks immediately (priming + first bar); poll until RUNNING.
    for _ in range(100):
        run = mcp_server.get_strategy_run(run_id)
        if run["status"] == "RUNNING":
            break
        time.sleep(0.05)
    else:
        pytest.fail("run did not reach RUNNING")

    stopped = mcp_server.stop_strategy(run_id)
    assert stopped["success"] is True
    assert stopped["run"]["status"] == "STOPPED"

    # The run detail carries the started/stopped timeline + events.
    detail = mcp_server.get_strategy_run(run_id)
    assert detail["started_at"]
    assert detail["stopped_at"]
    assert any(e["type"] == "state_change" for e in detail["events"])


def _wait_terminal(run_id: str, timeout_polls: int = 200) -> dict:
    for _ in range(timeout_polls):
        detail = mcp_server.get_strategy_run(run_id)
        if detail["status"] in ("COMPLETED", "FAILED"):
            return detail
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not reach a terminal state")


def test_run_backtest_transmits_risk_and_cost_model(mcp):
    """risk / cost_model 必须真的送进 HTTP 请求，而不是只写在文档里。

    判据放在 **run.params 快照**上：路由层只在字段存在时写快照（"没有这个字段"
    与"显式 enabled=false"是两个不同事实），所以 params 里出现这两个键，就证明
    MCP 工具确实透传了；反过来，不传时 params 里不该出现。
    """
    from datetime import date, timedelta

    created = mcp_server.create_strategy(name="risk bt", spec=DCA_SPEC)
    sid = created["strategy"]["id"]
    end = date.today()
    start = end - timedelta(days=20)

    risk = {"stop_loss_pct": 0.05, "trailing_stop_pct": 0.10, "cooldown_bars": 2}
    cost = {"enabled": True, "commission_rate": 0.00025,
            "stamp_tax_rate": 0.0005, "slippage_bps": 5}

    started = mcp_server.run_backtest(
        sid, start.isoformat(), end.isoformat(), risk=risk, cost_model=cost
    )
    assert started["success"] is True, started
    # 起始返回体就该带快照——不必等回测跑完才能证明透传。
    params = started["run"]["params"]
    # 快照是 JSON 形态：Decimal 序列化成字符串（"0.05"），比较前先转 float。
    assert float(params["risk"]["stop_loss_pct"]) == pytest.approx(0.05)
    assert float(params["risk"]["trailing_stop_pct"]) == pytest.approx(0.10)
    assert params["risk"]["cooldown_bars"] == 2
    assert params["cost_model"]["enabled"] is True
    assert float(params["cost_model"]["slippage_bps"]) == pytest.approx(5)

    # 反向：不传就不该凭空出现（否则"显式关掉成本"与"根本没配"会被抹平）。
    plain = mcp_server.run_backtest(sid, start.isoformat(), end.isoformat())
    assert plain["success"] is True, plain
    plain_params = plain["run"]["params"]
    assert "risk" not in plain_params
    assert "cost_model" not in plain_params

    # 让后台任务收尾，避免残留运行干扰同文件其他用例。
    _wait_terminal(started["run"]["id"])
    _wait_terminal(plain["run"]["id"])


def test_run_backtest_rejects_out_of_range_risk(mcp):
    """越界的止损值必须被服务端拒掉，且错误回给调用方——不是静默忽略。"""
    from datetime import date, timedelta

    created = mcp_server.create_strategy(name="bad risk bt", spec=DCA_SPEC)
    sid = created["strategy"]["id"]
    end = date.today()
    start = end - timedelta(days=20)

    result = mcp_server.run_backtest(
        sid, start.isoformat(), end.isoformat(), risk={"stop_loss_pct": 1.5}
    )
    assert result.get("error") is True, result


def test_start_strategy_transmits_risk_overlay(mcp):
    """`start_strategy(risk=...)` 要把覆盖送到 run.params（live 止损的唯一入口）。

    引擎侧两条轨（单标的 / 权重）早就在读 `run.params["risk"]`，但入口此前不存在，
    所以 live 永远配不上止损。这条钉住入口；"写进去真会平仓"由
    `tests/e2e/test_strategy_paper_runs.py::test_live_risk_overlay_closes_the_position_on_a_gap_bar`
    钉住（那边需要控价，只有 e2e 能做）。
    """
    created = mcp_server.create_strategy(name="risk live", spec=DCA_SPEC)
    sid = created["strategy"]["id"]
    accounts = mcp_server.list_accounts()
    if not accounts:
        r = requests.post(
            f"{mcp_server.PAPER_TRADING_API_URL}/api/v1/accounts",
            json={"name": "paper"},
            headers={"Authorization": f"Bearer {mcp_server.PAPER_TRADING_API_TOKEN}"},
            timeout=5,
        )
        assert r.status_code in (200, 201), r.text
        accounts = mcp_server.list_accounts()
    account_id = accounts[0]["id"]

    risk = {"stop_loss_pct": 0.07, "trailing_stop_pct": 0.12, "cooldown_bars": 3}
    started = mcp_server.start_strategy(sid, account_id, risk=risk)
    assert started.get("success") is True, started
    params = started["run"]["params"]
    # 快照是 JSON 形态：Decimal 序列化成字符串（"0.07"），比较前先转 float。
    assert float(params["risk"]["stop_loss_pct"]) == pytest.approx(0.07)
    assert float(params["risk"]["trailing_stop_pct"]) == pytest.approx(0.12)
    assert params["risk"]["cooldown_bars"] == 3

    mcp_server.stop_strategy(started["run"]["id"])

    # 不传 risk → params 里没有这个键（"没配"与"配了空"是两个事实）。
    plain = mcp_server.start_strategy(sid, account_id)
    assert plain.get("success") is True, plain
    assert "risk" not in plain["run"]["params"], plain["run"]["params"]
    mcp_server.stop_strategy(plain["run"]["id"])
