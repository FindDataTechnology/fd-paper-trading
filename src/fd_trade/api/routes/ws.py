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
"""

import asyncio
import json
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ...auth.security import TokenError, decode_access_token
from ...persistence.repositories import UserRepository
from ...realtime.manager import ClientConnection, ConnectionManager

router = APIRouter()

_AUTH_TIMEOUT = 5.0


def _manager(websocket: WebSocket) -> ConnectionManager:
    return websocket.app.state.realtime


async def _authenticate(websocket: WebSocket) -> uuid.UUID | None:
    """Resolve a user id from the first auth message, or ``None`` to reject."""
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
    return user.id


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    user_id = await _authenticate(websocket)
    if user_id is None:
        await websocket.close(code=4401)
        return

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
                conn.subscribed.add(symbol)
            elif mtype == "unsubscribe":
                conn.subscribed.discard(symbol)
    except WebSocketDisconnect:
        pass
    finally:
        await manager.remove(conn)
