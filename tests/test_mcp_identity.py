"""Identity-delegation tests for the paper-trading MCP tools.

Two registered users on one live server; the MCP layer must forward the
caller's own credential (header transport) instead of the configured fallback
token, so users cannot see each other's accounts/orders/positions. The
headerless path (stdio) keeps working via the configured token; with no
credential anywhere the call fails closed.
"""

import asyncio
import os
import socket
import threading
import time

import pytest
import requests
from fastmcp.exceptions import ToolError

os.environ.setdefault("PAPER_TRADING_API_TOKEN", "placeholder")  # module import
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
os.environ["no_proxy"] = "127.0.0.1,localhost"

from fd_paper_trading.api.app import create_app  # noqa: E402
from fd_paper_trading.mcp import server as mcp_server  # noqa: E402
from fd_paper_trading.persistence.base import init_db  # noqa: E402

_PASSWORD = "password123"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    """The app served by uvicorn in a daemon thread, with its own sqlite DB."""
    import uvicorn

    db_path = tmp_path_factory.mktemp("mcp-id") / "test.db"
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
def two_users(live_server):
    """Register two users; return {email: (jwt, api_token)} per user."""
    out = {}
    for email in ("alice@x.io", "bob@x.io"):
        requests.post(
            f"{live_server}/api/v1/auth/register",
            json={"email": email, "password": _PASSWORD},
            timeout=5,
        )
        r = requests.post(
            f"{live_server}/api/v1/auth/login",
            json={"email": email, "password": _PASSWORD},
            timeout=5,
        )
        jwt = r.json()["access_token"]
        r = requests.post(
            f"{live_server}/api/v1/tokens",
            json={"name": "mcp"},
            headers={"Authorization": f"Bearer {jwt}"},
            timeout=5,
        )
        out[email] = (jwt, r.json()["token"])
    return out


@pytest.fixture(autouse=True)
def _restore_fallback(live_server):
    """Point the module at the live server and keep the fallback stable."""
    saved_token = mcp_server.PAPER_TRADING_API_TOKEN
    saved_url = mcp_server.PAPER_TRADING_API_URL
    mcp_server.PAPER_TRADING_API_URL = live_server
    yield
    mcp_server.PAPER_TRADING_API_TOKEN = saved_token
    mcp_server.PAPER_TRADING_API_URL = saved_url


def _tool_call(live_server, token, fn, *args, **kwargs):
    """Invoke an MCP tool with an inbound Authorization header, as HTTP would.

    fastmcp resolves the request from a ContextVar; direct tool invocation has
    none, so set one carrying the caller's credential, the same way the HTTP
    transport would deliver it.
    """
    from fastmcp.server.http import _current_http_request

    class _FakeRequest:
        def __init__(self, bearer):
            self.headers = {"Authorization": f"Bearer {bearer}"}

    token_reset = _current_http_request.set(_FakeRequest(token))
    try:
        return fn(*args, **kwargs)
    finally:
        _current_http_request.reset(token_reset)


def test_inbound_credential_wins_over_fallback(live_server, two_users):
    _, bob_token = two_users["bob@x.io"]
    # A wrong fallback token is configured; the inbound credential must win.
    mcp_server.PAPER_TRADING_API_TOKEN = "invalid-fallback"
    result = _tool_call(live_server, bob_token, mcp_server.list_accounts)
    assert isinstance(result, list), result


def test_two_users_are_isolated(live_server, two_users):
    alice_jwt, alice_token = two_users["alice@x.io"]
    bob_jwt, bob_token = two_users["bob@x.io"]

    # Alice opens an account and places an order (market buy via the tool).
    created = _tool_call(live_server, alice_token, mcp_server.create_account, "main")
    assert created.get("id"), created
    alice_account = created["id"]

    order = _tool_call(
        live_server,
        alice_token,
        mcp_server.place_order,
        account_id=alice_account,
        symbol="AAPL",
        side="BUY",
        order_type="MARKET",
        quantity=1,
    )
    assert order.get("success") is True, order

    # Bob, calling as himself, sees no accounts at all — and Alice's account
    # is not addressable by him.
    bob_accounts = _tool_call(live_server, bob_token, mcp_server.list_accounts)
    assert bob_accounts == []

    r = requests.get(
        f"{live_server}/api/v1/accounts/{alice_account}",
        headers={"Authorization": f"Bearer {bob_token}"},
        timeout=5,
    )
    assert r.status_code == 404, r.text

    bob_positions = _tool_call(
        live_server, bob_token, mcp_server.list_positions, alice_account
    )
    assert bob_positions == []


def test_headerless_call_uses_fallback_token(live_server, two_users):
    alice_jwt, alice_token = two_users["alice@x.io"]
    # stdio shape: no request context at all; the configured token is used.
    mcp_server.PAPER_TRADING_API_TOKEN = alice_token
    accounts = mcp_server.list_accounts()
    assert isinstance(accounts, list)
    assert any(a["name"] == "main" for a in accounts)


def test_no_credential_anywhere_fails_closed(live_server):
    mcp_server.PAPER_TRADING_API_TOKEN = None
    # An error-flagged tool result, not an ordinary one: the consuming platform
    # retires credentials on failures, and list tools must not read as "empty".
    with pytest.raises(ToolError, match="missing_credential"):
        mcp_server._api_call("GET", "/api/v1/accounts")


def test_revoked_credential_fails_as_error_not_empty(live_server, two_users):
    """A rejected credential must surface as a tool error carrying 401 wording.

    The platform's stale-credential flip keys on a failed tool result whose text
    matches /\\b401\\b|unauthoriz/i; returning [] would read as "no accounts"
    and hide the rejection (the exact silent failure this asserts against).
    """
    alice_jwt, alice_token = two_users["alice@x.io"]
    # Create an account so a working call would return a non-empty list.
    _tool_call(live_server, alice_token, mcp_server.create_account, "revoke-me")

    r = requests.get(
        f"{live_server}/api/v1/tokens",
        headers={"Authorization": f"Bearer {alice_jwt}"},
        timeout=5,
    )
    token_id = next(t["id"] for t in r.json() if t.get("revoked_at") is None)
    r = requests.delete(
        f"{live_server}/api/v1/tokens/{token_id}",
        headers={"Authorization": f"Bearer {alice_jwt}"},
        timeout=5,
    )
    assert r.status_code == 204, r.text

    with pytest.raises(ToolError) as excinfo:
        _tool_call(live_server, alice_token, mcp_server.list_accounts)
    text = str(excinfo.value)
    assert "401" in text and "unauthoriz" in text.lower(), text


def test_new_tools_present(live_server):
    assert hasattr(mcp_server, "create_account")
    assert hasattr(mcp_server, "list_positions")
    assert hasattr(mcp_server, "search_instruments")
    assert hasattr(mcp_server, "get_bars")
    # deposit/reset stay REST-only
    assert not hasattr(mcp_server, "deposit")
    assert not hasattr(mcp_server, "reset_account")
