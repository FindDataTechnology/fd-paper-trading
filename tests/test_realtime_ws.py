"""WebSocket /ws streaming endpoint (task 4.5).

Drives the real ``ws_endpoint`` and ``ConnectionManager`` with a fake
WebSocket so the tests stay fully async and transport-free.
"""

import asyncio
import json
import types

import pytest
from starlette.websockets import WebSocketDisconnect

from fd_paper_trading.api.routes.ws import ws_endpoint, _AUTH_TIMEOUT  # noqa: F401
from fd_paper_trading.api.routes import ws as ws_module

_DISCONNECT = object()


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
        await asyncio.sleep(0.05)
        await app.state.realtime._emit_ticks()
        await asyncio.sleep(0.05)
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
        await asyncio.sleep(0.03)
        await app.state.realtime._emit_ticks()
        await asyncio.sleep(0.03)
        assert any(e["type"] == "quote" for e in fake.sent)

        fake.sent.clear()
        await fake.feed(json.dumps({"type": "unsubscribe", "symbol": "AAPL"}))
        await asyncio.sleep(0.03)
        await app.state.realtime._emit_ticks()
        await asyncio.sleep(0.03)
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
