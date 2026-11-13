"""WebSocket streaming endpoint at /ws.

Protocol:
- On connect the client must send a JSON auth message
  ``{"type": "auth", "token": "<jwt>"}`` within a short window; otherwise the
  connection is closed (code 4401).
- Subsequent messages: ``{"type": "subscribe", "symbol": "AAPL"}`` and
  ``{"type": "unsubscribe", "symbol": "AAPL"}``.
- Server pushes ``{"type": "quote", ...}`` ticks for subscribed symbols and
  ``{"type": "order", "order": {...}}`` events for the user's own order/fill
  changes.
- The group's ``max_subscriptions`` caps the distinct symbols one user may hold
  across all of their connections; a subscribe past the cap is answered with
  ``{"type": "error", "code": "subscription_limit", ...}`` and the socket stays
  open with its existing subscriptions.
"""

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ...auth.security import TokenError, decode_access_token
from ...persistence.models import DEFAULT_GROUP_CONFIG, User, group_config
from ...persistence.repositories import UserRepository
from ...realtime.manager import ClientConnection, ConnectionManager

router = APIRouter()

logger = logging.getLogger(__name__)

_AUTH_TIMEOUT = 5.0


def _subscription_limit(user: User) -> int:
    """Resolve ``max_subscriptions`` from the user's group, tolerating junk config.

    A group's ``config`` is free-form JSONB (unknown keys are preserved verbatim),
    so a hand-edited ``"max_subscriptions": "fifty"`` must not take the socket
    down: fall back to the default and say so in the log.
    """
    raw = group_config(user, "max_subscriptions")
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "group config max_subscriptions=%r is not an int; using %d",
            raw, DEFAULT_GROUP_CONFIG["max_subscriptions"],
        )
        return DEFAULT_GROUP_CONFIG["max_subscriptions"]



def _manager(websocket: WebSocket) -> ConnectionManager:
    return websocket.app.state.realtime


async def _authenticate(websocket: WebSocket) -> tuple[uuid.UUID, int] | None:
    """Resolve ``(user id, subscription limit)`` from the first auth message.

    Returns ``None`` to reject. The limit is resolved **inside** the session: the
    group is selectin-loaded there, and reading it off a detached ORM object after
    the session closes would be a lazy load against a closed connection.
    """
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=_AUTH_TIMEOUT)
    except (asyncio.TimeoutError, WebSocketDisconnect):
        return None
    try:
        msg = json.loads(raw)
        token = msg.get("token") if isinstance(msg, dict) and msg.get("type") == "auth" else None
    except (ValueError, TypeError):
        return None
    if not token:
        return None
    try:
        payload = decode_access_token(token)
        user_id = uuid.UUID(payload["sub"])
    except (TokenError, ValueError, KeyError):
        return None
    db = websocket.app.state.db
    async with db.session_factory() as session:
        user = await UserRepository(session).get_by_id(user_id)
        if user is None or not user.is_active:
            return None
        return user.id, _subscription_limit(user)


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    identity = await _authenticate(websocket)
    if identity is None:
        await websocket.close(code=4401)
        return
    user_id, max_subscriptions = identity

    manager = _manager(websocket)
    conn = ClientConnection(websocket=websocket, user_id=user_id)
    await manager.add(conn)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            symbol = msg.get("symbol")
            if not symbol or not isinstance(symbol, str):
                continue
            mtype = msg.get("type")
            if mtype == "subscribe":
                if await manager.try_subscribe(conn, symbol, max_subscriptions):
                    continue
                # Refuse the extra symbol but keep the socket and the existing
                # subscriptions: closing would also cut this user's other tabs,
                # and the client could not tell why.
                await websocket.send_json({
                    "type": "error",
                    "code": "subscription_limit",
                    "symbol": symbol,
                    "limit": max_subscriptions,
                    "message": (
                        f"subscription limit reached: {max_subscriptions} distinct "
                        "symbols per user; unsubscribe before subscribing to more"
                    ),
                })
            elif mtype == "unsubscribe":
                conn.subscribed.discard(symbol)
    except WebSocketDisconnect:
        pass
    finally:
        await manager.remove(conn)
