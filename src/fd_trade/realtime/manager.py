"""WebSocket connection manager for the /ws streaming endpoint.

Responsibilities:
- Track authenticated client connections and their per-symbol subscriptions.
- Broadcast order/fill events to the owning user's connections only.
- Emit periodic quote ticks for subscribed symbols from the market-data
  provider (with a small deterministic wobble so the simulated feed looks
  alive; the provider's ``get_quote`` itself stays static for fill/tests).

All access to the connection list is serialized with a lock; individual sends
are best-effort and drop the connection on failure.
"""

import asyncio
import math
import time
import uuid
from dataclasses import dataclass, field

from fastapi import WebSocket

from ..marketdata import MarketDataProvider

_QUOTE_Q = 2  # decimal places for streamed quote prices


@dataclass
class ClientConnection:
    """A single authenticated WebSocket client."""

    websocket: WebSocket
    user_id: uuid.UUID
    subscribed: set[str] = field(default_factory=set)


class ConnectionManager:
    def __init__(
        self, provider: MarketDataProvider, tick_interval: float = 1.0
    ) -> None:
        self.provider = provider
        self.tick_interval = tick_interval
        self._connections: list[ClientConnection] = []
        self._lock = asyncio.Lock()
        self._tick_task: asyncio.Task | None = None
        self._tick_count = 0

    # ---- lifecycle ----

    async def start(self) -> None:
        if self._tick_task is None or self._tick_task.done():
            self._tick_task = asyncio.create_task(self._tick_loop())

    async def stop(self) -> None:
        if self._tick_task is not None:
            self._tick_task.cancel()
            try:
                await self._tick_task
            except asyncio.CancelledError:
                pass
            self._tick_task = None

    # ---- connection bookkeeping ----

    async def add(self, conn: ClientConnection) -> None:
        async with self._lock:
            self._connections.append(conn)

    async def remove(self, conn: ClientConnection) -> None:
        async with self._lock:
            if conn in self._connections:
                self._connections.remove(conn)

    async def connection_count(self) -> int:
        async with self._lock:
            return len(self._connections)

    # ---- broadcasting ----

    async def broadcast_to_user(self, user_id: uuid.UUID, event: dict) -> None:
        """Send ``event`` to every connection owned by ``user_id``."""
        async with self._lock:
            targets = [c for c in self._connections if c.user_id == user_id]
        if targets:
            await asyncio.gather(*(self._safe_send(c, event) for c in targets))

    async def _safe_send(self, conn: ClientConnection, event: dict) -> None:
        try:
            await conn.websocket.send_json(event)
        except Exception:
            await self.remove(conn)

    # ---- quote tick loop ----

    async def _tick_loop(self) -> None:
        while True:
            await asyncio.sleep(self.tick_interval)
            self._tick_count += 1
            await self._emit_ticks()

    async def _emit_ticks(self) -> None:
        async with self._lock:
            # Aggregate subscribed symbols across all connections.
            subs: dict[str, list[ClientConnection]] = {}
            for conn in self._connections:
                for symbol in conn.subscribed:
                    subs.setdefault(symbol, []).append(conn)
        if not subs:
            return

        for symbol, conns in subs.items():
            quote = await self.provider.get_quote(symbol)
            if quote is None:
                continue
            bid, ask = self._wobble(symbol, quote)
            event = {
                "type": "quote",
                "symbol": symbol,
                "bid": bid,
                "ask": ask,
                "ts": int(time.time()),
            }
            # Fan out concurrently so one slow/stalled client can't delay the
            # rest of the tick cycle.
            await asyncio.gather(*(self._safe_send(conn, event) for conn in conns))

    def _wobble(self, symbol: str, quote) -> tuple[str, str]:
        """Apply a small deterministic wobble around the quote mid.

        Keeps ``get_quote`` (and thus fills/tests) static while making the
        streamed tick series visibly move. Amplitude ~0.1% of mid.
        """
        mid = float((quote.bid + quote.ask) / 2)
        spread = float((quote.ask - quote.bid) / 2)
        phase = (int.from_bytes(symbol.encode("utf-8"), "little") % 360) * (
            math.pi / 180
        )
        mid += math.sin(self._tick_count * 0.1 + phase) * mid * 0.001
        bid = mid - spread
        ask = mid + spread
        return f"{bid:.{_QUOTE_Q}f}", f"{ask:.{_QUOTE_Q}f}"
