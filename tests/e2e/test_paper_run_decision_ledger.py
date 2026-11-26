"""E2E: 纸面运行 → 决策账本落账（spec `paper-run-decision-ledger` 任务 3.1–3.4）。

驱动方式与 test_strategy_paper_runs 相同（直接建 run 行 + ``_tick_once``），账本侧
用 `httpx.MockTransport` 假端点接住 `record_decision` 调用——判据对象是**我们发出去
的记录形状与失败留痕**：真进程能证明"通了"，却证明不了"落了什么、落进了谁的库"，
方向是反的。（任务 5.1 的真账本检索在测试环境验收时跑，见 docs/ 的验收记录。）
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from fd_paper_trading.execution import PaperExecutionService
from fd_paper_trading.marketdata import SimulatedMarketDataProvider
from fd_paper_trading.marketdata.provider import Bar
from fd_paper_trading.persistence.models import (
    StrategyRun,
    StrategyRunMode,
    StrategyRunStatus,
)
from fd_paper_trading.persistence.repositories import StrategyRunRepository
from fd_paper_trading.strategy import ledger_plane

UTC = timezone.utc

# 5 根日 bar：2026-01-01 .. 01-05。首个 tick 只处理最新一根（01-05），every_n_bars=1
# ⇒ 该日就是调仓日，目标 {600000: 0.5, 600036: 0.5}。
WEIGHT_SPEC_TWO = {
    "kind": "weight",
    "universe": ["600000", "600036"],
    "rebalance": {"every_n_bars": 1},
    "weights_series": {"2026-01-01": {"600000": 0.5, "600036": 0.5}},
}

_DCA = {
    "kind": "template",
    "template": "dca",
    "symbol": "600000",
    "params": {"interval_bars": 1, "quantity": 1},
}


class _ControlledProvider(SimulatedMarketDataProvider):
    """SIM provider，日 bar 序列由测试全控；可选按标的区分序列（无覆盖能力）。"""

    def __init__(self, *, bars_by_symbol: dict[str, list[Bar]] | None = None):
        super().__init__()
        self._multi = bars_by_symbol or {}

    async def get_bars(self, symbol, timeframe, limit):
        bars = self._multi.get(symbol) or next(iter(self._multi.values()), None) or []
        return bars[-limit:] if limit else list(bars)


class _CoverageProvider(_ControlledProvider):
    """带覆盖事实能力的 provider（get_bars_with_coverage），口径由测试给定。"""

    def __init__(self, *, coverage: dict, **kw):
        super().__init__(**kw)
        self.coverage = coverage

    async def get_bars_with_coverage(self, symbol, start, end):
        return dict(self.coverage)


class _FakeLedger:
    """假账本端点：接住 record_decision 调用；fail=True 时整端点 503。"""

    def __init__(self):
        self.records: list[dict] = []
        self.headers: list[str] = []

    def transport(self, *, fail: bool = False) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.headers.append(request.headers.get("x-fd-caller") or "")
            payload = json.loads(request.content or b"{}")
            method = payload.get("method")
            if method == "initialize":
                return httpx.Response(
                    200, headers={"mcp-session-id": "sid-1"},
                    json={"jsonrpc": "2.0", "id": 1, "result": {}})
            if method == "notifications/initialized":
                return httpx.Response(202)
            if fail:
                return httpx.Response(503)
            self.records.append(payload["params"]["arguments"])
            body = ('event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"content":'
                    '[{"type":"text","text":"{\\"created\\": true, \\"decision_id\\": '
                    '\\"dec-test\\"}"}]}}\n\n')
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, text=body)

        return httpx.MockTransport(handler)


def _bars(n: int, start: date = date(2026, 1, 1)) -> list[Bar]:
    out: list[Bar] = []
    d = start
    base = 100.0
    for i in range(n):
        open_ = base + i
        ts = int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())
        out.append(Bar(ts=ts, open=Decimal(str(open_)), high=Decimal(str(open_ + 1)),
                       low=Decimal(str(open_ - 1)), close=Decimal(str(open_ + 0.5)),
                       volume=Decimal(1000)))
        d = d + timedelta(days=1)
    return out


async def _wire(app, provider) -> None:
    app.state.market_data_provider = provider
    app.state.execution_service = PaperExecutionService(provider=provider)
    app.state.strategy_runner.poll_seconds = 0.01
    app.state.strategy_runner.lookback_bars = 10


async def _authed(client):
    await client.post("/api/v1/auth/register",
                      json={"email": "u@x.io", "password": "password123"})
    r = await client.post("/api/v1/auth/login",
                          json={"email": "u@x.io", "password": "password123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _make_run(app, client, h, spec, universe, params_extra=None) -> uuid.UUID:
    r = await client.post("/api/v1/strategies",
                          json={"name": "wr", "spec": spec}, headers=h)
    strat = r.json()
    a = await client.post("/api/v1/accounts", json={"name": "paper"}, headers=h)
    async with app.state.db.session_factory() as session:
        run = StrategyRun(
            strategy_id=uuid.UUID(strat["id"]), strategy_version=1,
            mode=StrategyRunMode.PAPER, account_id=uuid.UUID(a.json()["id"]),
            status=StrategyRunStatus.STARTING,
            params={"universe": list(universe), **(params_extra or {})},
            universe=list(universe),
        )
        session.add(run)
        await session.commit()
        return run.id


async def _run_detail(client, h, run_id):
    r = await client.get(f"/api/v1/strategy-runs/{run_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


async def _orders_for_run(client, h, run_id):
    """按 strategy_run_id 过滤的订单（与 test_strategy_paper_runs 同一端点）。"""
    r = await client.get("/api/v1/accounts", headers=h)
    account_id = r.json()[0]["id"]
    r = await client.get(
        f"/api/v1/accounts/{account_id}/orders",
        params={"strategy_run_id": str(run_id)}, headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _events(app, run_id):
    async with app.state.db.session_factory() as session:
        return await StrategyRunRepository(session).list_events(run_id)


@pytest.fixture
def fake_ledger(monkeypatch):
    """把 runner 用的账本面接到假端点上（transport 注入走同一入口）。"""
    fake = _FakeLedger()
    import fd_paper_trading.strategy.runner as runner_mod

    original = runner_mod.ledger_plane.record_decision

    def patched(**kwargs):
        kwargs["transport"] = fake.transport()
        kwargs.setdefault("base_url", "http://fake-ledger/mcp")
        return original(**kwargs)

    monkeypatch.setattr(runner_mod.ledger_plane, "record_decision", patched)
    return fake


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (ledger_plane.LEDGER_ENDPOINT_ENV, ledger_plane.COMPUTE_ENDPOINT_ENV):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("FD_QUANT_CALLER_HMAC_KEY", "test-shared-key")
    yield


# ---------------------------------------------------------------------------
# 任务 3.1：权重轨 → 组合记录（TargetWeights 落账，产出方 = 策略运行本身）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_weight_rebalance_records_target_weights_in_the_user_ledger(
    client, app, fake_ledger):
    provider = _ControlledProvider(
        bars_by_symbol={"600000": _bars(5), "600036": _bars(5)})
    await _wire(app, provider)
    h = await _authed(client)
    run_id = await _make_run(app, client, h, WEIGHT_SPEC_TWO, ["600000", "600036"])

    await app.state.strategy_runner._tick_once(run_id)

    assert len(fake_ledger.records) == 1, "一个调仓点落一条组合记录"
    rec = fake_ledger.records[0]
    assert rec["record_kind"] == "portfolio"
    assert rec["portfolio_key"] == f"strategy_run:{run_id}"
    assert rec["pack"] == "fd-paper-trading"
    assert rec["skill"] == "strategy-runner"
    assert rec["as_of"] == "2026-01-05"
    # TargetWeights 契约实例随记录落账（走账本工具的专用槽位）
    tw = rec["target_weights"]
    assert tw["universe"] == ["600000", "600036"]
    assert tw["weights"] == {"600000": 0.5, "600036": 0.5}
    assert tw["producer"] == {"pack": "fd-paper-trading",
                              "skill": "strategy-runner", "run_id": str(run_id)}
    # payload 是 PortfolioIntent：derived_from 指向该 run
    assert rec["payload"]["derived_from"] == [f"strategy_run:{run_id}"]
    assert rec["payload"]["weights"] == tw["weights"]
    # 每个请求都带按用户现签的验签头（落进用户自己的库，不是服务库）
    assert fake_ledger.headers and all(fake_ledger.headers)
    user_id, _ts, sig = fake_ledger.headers[0].rsplit(".", 2)
    assert user_id == "u@x.io" and len(sig) == 64
    # 双计数进 run.params（任务 3.3 的成功形态）；provider 无覆盖能力 ⇒ 带原因标注
    detail = await _run_detail(client, h, run_id)
    assert detail["params"]["ledger"] == {
        "attempted": 1, "created": 1, "failed": 0, "last_error": None,
        "series_ref_unavailable": "provider_declares_no_series_ref",
    }


# ---------------------------------------------------------------------------
# 任务 3.2：跳过（无价格）不落账——账本记录数 = 实际执行指令数
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skipped_symbol_is_not_recorded(client, app, fake_ledger):
    # 600036 缺 01-05 的 bar：调仓日它是 stale——照计价但不交易（SKIP），600000 照常下
    provider = _ControlledProvider(
        bars_by_symbol={"600000": _bars(5), "600036": _bars(4)})
    await _wire(app, provider)
    h = await _authed(client)
    run_id = await _make_run(app, client, h, WEIGHT_SPEC_TWO, ["600000", "600036"])

    await app.state.strategy_runner._tick_once(run_id)

    detail = await _run_detail(client, h, run_id)
    assert detail["params"]["ledger"]["attempted"] == 1, "实际执行指令 1 条 → 尝试 1 条"
    assert detail["params"]["ledger"]["created"] == 1
    events = await _events(app, run_id)
    skips = [e for e in events if e.type == "skip" and "600036" in e.message]
    assert skips, "被跳过的标的应有 SKIP 事件点名原因"
    # 交易照常：600000 的订单下出去了（被跳过的 600036 没有）
    orders = await _orders_for_run(client, h, run_id)
    assert any(o["symbol"] == "600000" for o in orders), "执行了的指令该有订单"
    assert not any(o["symbol"] == "600036" for o in orders), "跳过的标的不该有订单"


# ---------------------------------------------------------------------------
# 任务 3.3：账本不可达 → 交易照常、失败留痕（事件 + 双计数）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unreachable_ledger_does_not_block_trading(client, app, monkeypatch):
    fake = _FakeLedger()
    import fd_paper_trading.strategy.runner as runner_mod

    original = runner_mod.ledger_plane.record_decision

    def patched(**kwargs):
        kwargs["transport"] = fake.transport(fail=True)
        kwargs.setdefault("base_url", "http://fake-ledger/mcp")
        return original(**kwargs)

    monkeypatch.setattr(runner_mod.ledger_plane, "record_decision", patched)
    provider = _ControlledProvider(
        bars_by_symbol={"600000": _bars(5), "600036": _bars(5)})
    await _wire(app, provider)
    h = await _authed(client)
    run_id = await _make_run(app, client, h, WEIGHT_SPEC_TWO, ["600000", "600036"])

    await app.state.strategy_runner._tick_once(run_id)

    detail = await _run_detail(client, h, run_id)
    ledger = detail["params"]["ledger"]
    assert ledger["attempted"] == 1 and ledger["created"] == 0 and ledger["failed"] == 1
    assert ledger["last_error"], "失败原因必须留痕"
    assert "503" in ledger["last_error"]
    # 交易照常完成：订单在（不回滚）
    orders = await _orders_for_run(client, h, run_id)
    assert orders, "账本挂了不该挡下单"
    # 失败发运行事件
    events = await _events(app, run_id)
    failures = [e for e in events if e.type == "error"
                and "decision ledger" in e.message]
    assert failures, "落账失败必须有一条可见的运行事件"
    assert failures[0].data["attempted"] == 1 and failures[0].data["failed"] == 1

@pytest.mark.asyncio
async def test_missing_hmac_key_counts_as_failed_no_request_sent(client, app,
                                                                 monkeypatch):
    """密钥未配 ⇒ 计失败留痕 no_identity_key，且**不发无头请求**（任务 2.2）。"""
    monkeypatch.delenv("FD_QUANT_CALLER_HMAC_KEY")
    fake = _FakeLedger()
    import fd_paper_trading.strategy.runner as runner_mod

    original = runner_mod.ledger_plane.record_decision

    def patched(**kwargs):
        kwargs["transport"] = fake.transport()
        kwargs.setdefault("base_url", "http://fake-ledger/mcp")
        return original(**kwargs)

    monkeypatch.setattr(runner_mod.ledger_plane, "record_decision", patched)
    provider = _ControlledProvider(
        bars_by_symbol={"600000": _bars(5), "600036": _bars(5)})
    await _wire(app, provider)
    h = await _authed(client)
    run_id = await _make_run(app, client, h, WEIGHT_SPEC_TWO, ["600000", "600036"])

    await app.state.strategy_runner._tick_once(run_id)

    assert fake.records == [] and fake.headers == [], "无头请求不该发出去"
    detail = await _run_detail(client, h, run_id)
    ledger = detail["params"]["ledger"]
    assert ledger["attempted"] == 1 and ledger["created"] == 0
    assert ledger["failed"] == 1
    assert "no_identity_key" in ledger["last_error"]
    events = await _events(app, run_id)
    assert any(e.type == "error" and "decision ledger" in e.message for e in events)
    # 交易照常：订单照下
    orders = await _orders_for_run(client, h, run_id)
    assert orders, "密钥缺失不该挡下单"


# ---------------------------------------------------------------------------
# 任务 3.4：series_ref 两种行情口径
# ---------------------------------------------------------------------------


def _ref_for(symbol: str) -> dict:
    from fd_paper_trading.marketdata.series_ref import build_series_ref

    ref, _ = build_series_ref(
        source_id="astock_daily", symbol=symbol, adjusted="forward",
        as_of_max="2026-01-05", window_start="2026-01-01",
        window_end="2026-01-05", row_count=5,
        adjustment_anchor="2026-01-05", db_source="astock_daily")
    return ref


@pytest.mark.asyncio
async def test_series_ref_rides_on_the_record_when_caliber_is_known(
    client, app, fake_ledger):
    ref = _ref_for("600000")
    provider = _CoverageProvider(
        bars_by_symbol={"600000": _bars(5), "600036": _bars(5)},
        coverage={"series_ref": ref, "missing": [], "market_closed": [],
                  "fingerprint": "", "adjusted": "forward",
                  "source": "astock_daily"},
    )
    await _wire(app, provider)
    h = await _authed(client)
    run_id = await _make_run(app, client, h, WEIGHT_SPEC_TWO, ["600000", "600036"])

    await app.state.strategy_runner._tick_once(run_id)

    rec = fake_ledger.records[0]
    assert rec["series_ref"]["fingerprint"] == ref["fingerprint"]
    assert rec["series_ref"]["source_id"] == "astock_daily"
    assert "series_ref_unavailable" not in (await _run_detail(
        client, h, run_id))["params"]["ledger"]


@pytest.mark.asyncio
async def test_series_ref_unavailable_is_marked_not_silently_dropped(
    client, app, fake_ledger):
    """口径不明 ⇒ 落账照常，但原因写进 run.params 的可机读字段（design D6）。"""
    provider = _CoverageProvider(
        bars_by_symbol={"600000": _bars(5), "600036": _bars(5)},
        coverage={"series_ref_unavailable": "caliber_unknown", "missing": [],
                  "market_closed": [], "fingerprint": "", "adjusted": "unknown",
                  "source": "astock_daily"},
    )
    await _wire(app, provider)
    h = await _authed(client)
    run_id = await _make_run(app, client, h, WEIGHT_SPEC_TWO, ["600000", "600036"])

    await app.state.strategy_runner._tick_once(run_id)

    rec = fake_ledger.records[0]
    assert "series_ref" not in rec, "出不来 ref 就不造一个"
    detail = await _run_detail(client, h, run_id)
    assert detail["params"]["ledger"]["series_ref_unavailable"] == "caliber_unknown"
    assert detail["params"]["ledger"]["created"] == 1, "缺出处不拦落账"


# ---------------------------------------------------------------------------
# 任务 3.1（单标的轨）：每个成为真实决策的信号一条 Rating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_symbol_signal_records_a_rating(client, app, fake_ledger):
    provider = _ControlledProvider(bars_by_symbol={"600000": _bars(5)})
    await _wire(app, provider)
    h = await _authed(client)
    run_id = await _make_run(app, client, h, _DCA, ["600000"])

    await app.state.strategy_runner._tick_once(run_id)

    assert len(fake_ledger.records) == 1, "DCA 每 bar 一个 BUY 信号 → 一条 Rating"
    rec = fake_ledger.records[0]
    assert rec["record_kind"] == "single"
    assert rec["symbol"] == "600000"
    assert rec["pack"] == "fd-paper-trading" and rec["skill"] == "strategy-runner"
    assert "target_weights" not in rec, "单标的轨不传 target_weights"
    payload = rec["payload"]
    assert payload["subject"] == "600000"
    assert payload["side"] == "buy"
    assert payload["producer"]["run_id"] == str(run_id)
    assert payload["basis"], "basis 必填"
    detail = await _run_detail(client, h, run_id)
    assert detail["params"]["ledger"]["created"] == 1
