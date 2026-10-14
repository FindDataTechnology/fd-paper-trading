"""Real-time WebSocket streaming.

A ``ConnectionManager`` tracks authenticated client connections (scoped to a
user) and their symbol subscriptions, broadcasts order/fill events to the owning
user, and emits periodic quote ticks for subscribed symbols from the
``MarketDataProvider`` (the simulated provider makes the streaming contract
exercisable without a real feed).
"""

from .manager import ClientConnection, ConnectionManager

__all__ = ["ClientConnection", "ConnectionManager"]
