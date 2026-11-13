"""End-to-end user flow (task 12.1).

Walks the whole chain a browser session would: register -> login -> create
account -> search a symbol -> fetch bars -> open a WS and subscribe -> place an
order -> receive the order/fill event over /ws -> see the position appear.
"""

import asyncio
import json
import types

from starlette.websockets import WebSocketDisconnect

from fd_paper_trading.api.routes.ws import ws_endpoint

_DISCONNECT = object()


class _FakeWS:
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

    async def feed(self, raw) -> None:
        await self._q.put(raw)


async def _wait_until(predicate, *, timeout: float = 3.0, interval: float = 0.01) -> bool:
    """有上限地等一个条件成立，成立即返回（不必睡满）。

    为什么不用固定 `sleep(0.05)`：2026-11-13 全量跑时这条用例偶发红在
    `assert any(... quote ...)` 上——`ws_endpoint` 是一个 task，它要先取走 auth、
    再取走 subscribe 才会把连接登记进 manager。负载高时 50ms 内这两步可能没排到，
    于是订阅尚未生效，后面所有依赖该连接的事件（quote、order）都不会到。
    固定睡眠既慢（快机器白等）又脆（慢机器不够）——换成轮询两头都对。
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return predicate()


def _subscribed(app, symbol: str) -> bool:
    """manager 里是否有任一连接订阅了该 symbol（即 subscribe 已被处理）。"""
    return any(
        symbol in c.subscribed for c in getattr(app.state.realtime, "_connections", ())
    )


async def test_full_trading_flow(client, register, login, auth_headers, create_account, place_order, app):
    # 1. Register + login.
    headers = await auth_headers("e2e@example.com")
    token = headers["Authorization"].split(" ", 1)[1]

    # 2. Create an account.
    acct = (await create_account(headers, starting_balance=100000)).json()
    aid = acct["id"]

    # 3. Search for a symbol.
    search = await client.get("/api/v1/instruments/search?q=AAP", headers=headers)
    assert search.status_code == 200
    assert any(h["symbol"] == "AAPL" for h in search.json())

    # 4. Fetch bars for the chart.
    bars = await client.get(
        "/api/v1/instruments/AAPL/bars?timeframe=5m&limit=50", headers=headers
    )
    assert bars.status_code == 200
    assert len(bars.json()) == 50

    # 5. Open the WebSocket, authenticate, and subscribe to AAPL.
    ws = _FakeWS(app.state)
    task = asyncio.create_task(ws_endpoint(ws))
    await ws.feed(json.dumps({"type": "auth", "token": token}))
    await ws.feed(json.dumps({"type": "subscribe", "symbol": "AAPL"}))
    # 等**订阅真的登记进 manager**，不是等一个固定时长（见 _wait_until 的说明）。
    assert await _wait_until(lambda: _subscribed(app, "AAPL")), "订阅未在时限内登记"
    # Simulated provider emits ticks for subscribed symbols.
    await app.state.realtime._emit_ticks()
    assert await _wait_until(
        lambda: any(e["type"] == "quote" and e["symbol"] == "AAPL" for e in ws.sent)
    ), f"未收到 AAPL 报价 tick；已收到 {[e['type'] for e in ws.sent]}"

    # 6. Place a market BUY order; the fill is broadcast over /ws.
    r = await place_order(headers, aid, symbol="AAPL", side="BUY", quantity="10")
    assert r.status_code == 202
    placed = r.json()
    assert placed["status"] == "FILLED"

    # 7. The order/fill event arrived over the WebSocket.
    assert await _wait_until(lambda: any(e["type"] == "order" for e in ws.sent)), (
        f"未收到订单事件；已收到 {[e['type'] for e in ws.sent]}"
    )
    order_events = [e for e in ws.sent if e["type"] == "order"]
    assert len(order_events) == 1
    assert order_events[0]["order"]["id"] == placed["id"]

    # 8. The order shows up in the account's orders list.
    listed = await client.get(f"/api/v1/accounts/{aid}/orders", headers=headers)
    assert any(o["id"] == placed["id"] for o in listed.json())

    # 9. A position was opened.
    positions = await client.get(f"/api/v1/accounts/{aid}/positions", headers=headers)
    assert any(p["symbol"] == "AAPL" for p in positions.json())

    # Tear down the WS task.
    await ws.feed(_DISCONNECT)
    await asyncio.sleep(0.02)
    if not task.done():
        task.cancel()
