"""WebSocket /ws streaming endpoint (task 4.5).

Drives the real ``ws_endpoint`` and ``ConnectionManager`` with a fake
WebSocket so the tests stay fully async and transport-free.
"""

import asyncio
import json
import time
import types
import uuid

import pytest
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from fd_paper_trading.api.routes.ws import ws_endpoint, _AUTH_TIMEOUT  # noqa: F401
from fd_paper_trading.api.routes import ws as ws_module
from fd_paper_trading.persistence.models import User, UserGroup
from fd_paper_trading.realtime import manager as manager_module
from fd_paper_trading.realtime.manager import ClientConnection, ConnectionManager

_DISCONNECT = object()


async def _wait_until(pred, *, timeout: float = 2.0, step: float = 0.01) -> bool:
    """等条件成立，而不是睡一个"通常够"的固定时长。

    固定 sleep 在机器负载高时会假红（实测全量跑偶发一次：订阅还没注册完就
    ``_emit_ticks()``，于是收不到 tick）。判据交给条件本身，超时给足 2 秒——
    放宽只影响"多久判失败"，不影响判定。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        await asyncio.sleep(step)
    return pred()


class FakeWebSocket:
    """Minimal stand-in for Starlette's WebSocket for direct handler testing."""

    def __init__(self, app_state) -> None:
        self.app = types.SimpleNamespace(state=app_state)
        self._q: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.accepted = False
        self.closed: int | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000) -> None:
        self.closed = code

    async def receive_text(self) -> str:
        item = await self._q.get()
        if item is _DISCONNECT:
            raise WebSocketDisconnect()
        return item

    async def send_json(self, event: dict) -> None:
        self.sent.append(event)

    async def feed(self, raw: str | object) -> None:
        await self._q.put(raw)


async def _connect(app, token: str):
    """Authenticate a fake connection and return (fake, task)."""
    fake = FakeWebSocket(app.state)
    task = asyncio.create_task(ws_endpoint(fake))
    await fake.feed(json.dumps({"type": "auth", "token": token}))
    await asyncio.sleep(0.05)  # let the handler authenticate + register
    return fake, task


async def _disconnect(fake, task) -> None:
    await fake.feed(_DISCONNECT)
    await asyncio.sleep(0.02)
    if not task.done():
        task.cancel()


async def _token_for(client, register, login, email="user@example.com") -> str:
    await register(email)
    r = await login(email)
    return r.json()["access_token"]


async def _set_group_config(app, email: str, config: dict) -> None:
    """Put ``email`` in a fresh group whose config is ``config``.

    The cap is a group-config key (spec: user-groups), so the test drives it the
    same way an admin would rather than monkeypatching the resolver — if the key
    name or the lookup path drifts, these tests go red.
    """
    async with app.state.db.session_factory() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        group = UserGroup(name=f"g-{uuid.uuid4().hex[:8]}", config=config)
        session.add(group)
        await session.flush()
        user.group_id = group.id
        await session.commit()


def _held(app) -> set[str]:
    """Every symbol currently subscribed across all connections."""
    return {
        s for c in app.state.realtime._connections for s in c.subscribed
    }


def _errors(fake) -> list[dict]:
    return [e for e in fake.sent if e.get("type") == "error"]


async def test_ws_rejects_when_no_auth_message(monkeypatch, app, client, register):
    monkeypatch.setattr(ws_module, "_AUTH_TIMEOUT", 0.2)
    fake = FakeWebSocket(app.state)
    task = asyncio.create_task(ws_endpoint(fake))
    await asyncio.sleep(0.4)
    assert fake.closed == 4401
    assert not fake.accepted or fake.closed is not None
    await asyncio.sleep(0)
    if not task.done():
        task.cancel()


async def test_ws_rejects_invalid_token(app, client, register):
    fake = FakeWebSocket(app.state)
    task = asyncio.create_task(ws_endpoint(fake))
    await fake.feed(json.dumps({"type": "auth", "token": "not-a-jwt"}))
    await asyncio.sleep(0.1)
    assert fake.closed == 4401
    await _disconnect(fake, task)


async def test_ws_accepts_valid_token_then_subscribe_receives_tick(
    app, client, register, login
):
    token = await _token_for(client, register, login)
    fake, task = await _connect(app, token)
    try:
        assert fake.accepted
        await fake.feed(json.dumps({"type": "subscribe", "symbol": "AAPL"}))
        # 等订阅真的注册上（不是"睡 50ms 通常够"）
        assert await _wait_until(
            lambda: any("AAPL" in c.subscribed
                        for c in app.state.realtime._connections)
        ), "subscribe 没有注册到连接上"
        await app.state.realtime._emit_ticks()
        assert await _wait_until(
            lambda: any(e["type"] == "quote" for e in fake.sent)
        ), "发了一次 tick 也没收到 quote"
        quotes = [e for e in fake.sent if e["type"] == "quote"]
        assert any(q["symbol"] == "AAPL" for q in quotes)
        assert "bid" in quotes[0] and "ask" in quotes[0]
    finally:
        await _disconnect(fake, task)


async def test_ws_unsubscribe_stops_ticks(app, client, register, login):
    token = await _token_for(client, register, login)
    fake, task = await _connect(app, token)
    try:
        await fake.feed(json.dumps({"type": "subscribe", "symbol": "AAPL"}))
        assert await _wait_until(
            lambda: any("AAPL" in c.subscribed
                        for c in app.state.realtime._connections)
        )
        await app.state.realtime._emit_ticks()
        assert await _wait_until(
            lambda: any(e["type"] == "quote" for e in fake.sent)
        )

        fake.sent.clear()
        await fake.feed(json.dumps({"type": "unsubscribe", "symbol": "AAPL"}))
        # 等退订真的生效（订阅集合里没有它了），再打一次 tick
        assert await _wait_until(
            lambda: not any("AAPL" in c.subscribed
                            for c in app.state.realtime._connections)
        ), "unsubscribe 没有生效"
        await app.state.realtime._emit_ticks()
        await asyncio.sleep(0.02)   # 让可能发出的帧落地，才能断言"没有"
        assert not any(e["type"] == "quote" for e in fake.sent)
    finally:
        await _disconnect(fake, task)


async def test_ws_order_event_delivered_only_to_owning_user(
    app, client, register, login, auth_headers, create_account, place_order
):
    alice_headers = await auth_headers("alice@example.com")
    alice_token = alice_headers["Authorization"].split(" ", 1)[1]
    bob_token = await _token_for(client, register, login, "bob@example.com")

    fake_a, task_a = await _connect(app, alice_token)
    fake_b, task_b = await _connect(app, bob_token)
    try:
        acct = (await create_account(alice_headers, starting_balance=100000)).json()
        r = await place_order(
            alice_headers, acct["id"], symbol="AAPL", side="BUY", quantity="10"
        )
        assert r.status_code == 202
        await asyncio.sleep(0.05)

        alice_orders = [e for e in fake_a.sent if e["type"] == "order"]
        bob_orders = [e for e in fake_b.sent if e["type"] == "order"]
        assert len(alice_orders) == 1
        assert alice_orders[0]["order"]["symbol"] == "AAPL"
        assert bob_orders == []
    finally:
        await _disconnect(fake_a, task_a)
        await _disconnect(fake_b, task_b)


# ---- subscription cap (spec: web-realtime "Bounded quote subscriptions") ----
#
# 未加这条判据前，一个客户端订阅 1000 个符号会让一个 tick 发 1000 次 get_quote、
# 耗时 1167 ms 超过 1 s 间隔（实测见 change proposal）——而 subscribe 只是
# 一句 `conn.subscribed.add(symbol)`。

async def test_subscribing_past_the_cap_is_refused_and_explained(
    app, client, register, login
):
    token = await _token_for(client, register, login)
    await _set_group_config(app, "user@example.com", {"max_subscriptions": 2})
    fake, task = await _connect(app, token)
    try:
        for symbol in ("AAPL", "MSFT"):
            await fake.feed(json.dumps({"type": "subscribe", "symbol": symbol}))
        assert await _wait_until(lambda: _held(app) == {"AAPL", "MSFT"}), (
            f"前两条订阅没登记上：{_held(app)}"
        )

        await fake.feed(json.dumps({"type": "subscribe", "symbol": "NVDA"}))
        assert await _wait_until(lambda: bool(_errors(fake))), (
            "越界订阅没有回错误事件（静默丢弃最难查）"
        )
        err = _errors(fake)[0]
        assert err["code"] == "subscription_limit"
        assert err["symbol"] == "NVDA"
        assert err["limit"] == 2
        assert "2" in err["message"], "错误消息要点名限额"
        assert _held(app) == {"AAPL", "MSFT"}, "越界的那条被加进去了"
        assert fake.closed is None, "超限不该掐断连接（会连累同用户其它标签页）"

        # 既有订阅照旧工作
        fake.sent.clear()
        await app.state.realtime._emit_ticks()
        assert await _wait_until(
            lambda: {e["symbol"] for e in fake.sent if e["type"] == "quote"}
            == {"AAPL", "MSFT"}
        ), "被拒一条之后既有订阅不再推送"
    finally:
        await _disconnect(fake, task)


async def test_the_cap_is_per_user_not_per_connection(app, client, register, login):
    """一个用户多开连接不该多拿预算——否则"每标签页一份"正是要堵的口子。"""
    token = await _token_for(client, register, login)
    await _set_group_config(app, "user@example.com", {"max_subscriptions": 1})

    fake_a, task_a = await _connect(app, token)
    fake_b, task_b = await _connect(app, token)
    try:
        await fake_a.feed(json.dumps({"type": "subscribe", "symbol": "AAPL"}))
        assert await _wait_until(lambda: _held(app) == {"AAPL"})

        # 第二个连接是同一个用户，预算已用尽
        await fake_b.feed(json.dumps({"type": "subscribe", "symbol": "MSFT"}))
        assert await _wait_until(lambda: bool(_errors(fake_b))), (
            "第二个连接拿到了独立预算（按连接计数就会这样）"
        )
        assert _held(app) == {"AAPL"}

        # 退订腾出名额后，第二个连接可以订阅
        await fake_a.feed(json.dumps({"type": "unsubscribe", "symbol": "AAPL"}))
        assert await _wait_until(lambda: _held(app) == set())
        fake_b.sent.clear()
        await fake_b.feed(json.dumps({"type": "subscribe", "symbol": "MSFT"}))
        assert await _wait_until(lambda: _held(app) == {"MSFT"}), (
            "腾出名额后仍订不上"
        )
        assert _errors(fake_b) == []
    finally:
        await _disconnect(fake_a, task_a)
        await _disconnect(fake_b, task_b)


async def test_resubscribing_a_held_symbol_at_the_cap_is_idempotent(
    app, client, register, login
):
    """前端重连后会重发活跃订阅；在限额上重发必须成功，否则是假错误。"""
    token = await _token_for(client, register, login)
    await _set_group_config(app, "user@example.com", {"max_subscriptions": 1})
    fake, task = await _connect(app, token)
    try:
        await fake.feed(json.dumps({"type": "subscribe", "symbol": "AAPL"}))
        assert await _wait_until(lambda: _held(app) == {"AAPL"})

        fake.sent.clear()
        await fake.feed(json.dumps({"type": "subscribe", "symbol": "AAPL"}))
        # 消息是按顺序消费的：再发一条**该被拒**的，它落地就证明前一条已处理。
        # 这样既不用固定 sleep，也不会把"重订阅没报错"误判成"重订阅没处理"。
        await fake.feed(json.dumps({"type": "subscribe", "symbol": "MSFT"}))
        assert await _wait_until(lambda: bool(_errors(fake))), "队列没推进"
        assert [e["symbol"] for e in _errors(fake)] == ["MSFT"], (
            f"重订阅被当成超限：{_errors(fake)}"
        )
        assert _held(app) == {"AAPL"}
    finally:
        await _disconnect(fake, task)


async def test_zero_disables_the_cap(app, client, register, login):
    token = await _token_for(client, register, login)
    await _set_group_config(app, "user@example.com", {"max_subscriptions": 0})
    fake, task = await _connect(app, token)
    try:
        symbols = {"AAPL", "MSFT", "NVDA", "AMZN"}
        for symbol in symbols:
            await fake.feed(json.dumps({"type": "subscribe", "symbol": symbol}))
        assert await _wait_until(lambda: _held(app) == symbols), (
            f"0 应表示不限：{_held(app)}"
        )
        assert _errors(fake) == []
    finally:
        await _disconnect(fake, task)


async def test_a_junk_limit_does_not_take_the_socket_down(app, client, register, login):
    """组配置是自由 JSONB；写成字符串也只是退回默认值，不该崩掉连接。"""
    token = await _token_for(client, register, login)
    await _set_group_config(app, "user@example.com", {"max_subscriptions": "fifty"})
    fake, task = await _connect(app, token)
    try:
        await fake.feed(json.dumps({"type": "subscribe", "symbol": "AAPL"}))
        assert await _wait_until(lambda: _held(app) == {"AAPL"}), (
            "垃圾配置把订阅路径带崩了"
        )
        assert fake.closed is None
    finally:
        await _disconnect(fake, task)


async def test_tick_fetch_is_concurrent_but_bounded():
    """串行取价会拖过 tick 间隔；无界并发又会一次索取整池连接。"""

    class Probe:
        def __init__(self) -> None:
            self.inflight = 0
            self.peak = 0
            self.calls = 0

        async def get_quote(self, symbol):
            self.calls += 1
            self.inflight += 1
            self.peak = max(self.peak, self.inflight)
            try:
                await asyncio.sleep(0.002)
            finally:
                self.inflight -= 1
            return None

    probe = Probe()
    manager = ConnectionManager(probe, tick_interval=1.0)
    conn = ClientConnection(
        websocket=types.SimpleNamespace(),
        user_id=uuid.uuid4(),
    )
    for i in range(40):
        conn.subscribed.add(f"{i:06d}")
    await manager.add(conn)

    await manager._emit_ticks()

    assert probe.calls == 40, "不是每个符号都取了一次价"
    assert probe.peak > 1, "取价是串行的（串行会拖过 tick 间隔）"
    assert probe.peak <= manager_module._TICK_FETCH_CONCURRENCY, (
        f"在途请求 {probe.peak} 超过上限 {manager_module._TICK_FETCH_CONCURRENCY}"
    )
