"""Shared test fixtures and helpers.

Tests run against an in-memory SQLite database via ``httpx``'s ASGI transport,
so no external Postgres or broker is required.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from fd_paper_trading.api.app import create_app
from fd_paper_trading.marketdata import SimulatedMarketDataProvider
from fd_paper_trading.persistence.base import init_db

TEST_DB_URL = "sqlite+aiosqlite://"


@pytest_asyncio.fixture
async def app():
    # Explicit simulated provider so tests never hit the network.
    application = create_app(
        database_url=TEST_DB_URL,
        market_data_provider=SimulatedMarketDataProvider(),
    )
    await init_db(application.state.db)
    yield application
    await application.state.db.engine.dispose()


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def register(client):
    async def _register(email="user@example.com", password="password123"):
        return await client.post(
            "/api/v1/auth/register", json={"email": email, "password": password}
        )

    return _register


@pytest.fixture
def login(client):
    async def _login(email="user@example.com", password="password123"):
        return await client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )

    return _login


@pytest.fixture
def auth_headers(register, login):
    async def _headers(email="user@example.com", password="password123"):
        await register(email, password)
        r = await login(email, password)
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    return _headers


@pytest.fixture
def create_account(client):
    async def _create(headers, name="default", starting_balance=None):
        payload: dict = {"name": name}
        if starting_balance is not None:
            payload["starting_balance"] = str(starting_balance)
        return await client.post("/api/v1/accounts", json=payload, headers=headers)

    return _create


@pytest.fixture
def place_order(client):
    async def _place(
        headers,
        account_id,
        symbol="AAPL",
        side="BUY",
        order_type="MARKET",
        quantity="10",
        limit_price=None,
        client_order_id=None,
    ):
        payload: dict = {
            "account_id": str(account_id),
            "symbol": symbol,
            "side": side,
            "order_type": order_type,
            "quantity": str(quantity),
        }
        if limit_price is not None:
            payload["limit_price"] = str(limit_price)
        if client_order_id is not None:
            payload["client_order_id"] = client_order_id
        return await client.post("/api/v1/orders", json=payload, headers=headers)

    return _place


# ===== Chat e2e test fixtures =====


@pytest.fixture
def litellm_mock():
    """Mock LiteLLM for deterministic chat testing."""

    class MockResponseChoice:
        def __init__(self, message):
            self.message = message

    class MockToolCallFunction:
        def __init__(self, name, arguments):
            self.name = name
            self.arguments = arguments

    class MockToolCall:
        def __init__(self, name, arguments):
            self.function = MockToolCallFunction(name, json.dumps(arguments))

    class MockCompletionResponse:
        def __init__(self, choices, tool_calls_response=None):
            if tool_calls_response:
                # Return a response that triggers tool calls
                message = type("obj", (object,), {"tool_calls": [tool_calls_response]})()
                self.choices = [MockResponseChoice(message)]
            else:
                content = tool_calls_response["return_content"] if isinstance(tool_calls_response, dict) else tool_calls_response
                message = type("obj", (object,), {"tool_calls": None, "content": content})()
                self.choices = [MockResponseChoice(message)]

        @classmethod
        def from_tool_call(cls, tool_name, tool_args, return_value, return_content=None):
            """Factory to create a mock response with tool call."""
            return cls(
                choices=None,
                tool_calls_response=cls._make_tool_call(tool_name, tool_args, return_value, return_content),
            )

        @staticmethod
        def _make_tool_call(tool_name, tool_args, return_value, return_content=None):
            class ToolCallWithResult:
                id = "call_abc123"
                function = type("obj", (object,), {"name": tool_name, "arguments": json.dumps(tool_args)})()
            return ToolCallWithResult()

    mock = MagicMock()
    mock.acompletion = AsyncMock(side_effect=lambda **kwargs: MockCompletionResponse(**kwargs) if kwargs.get("choices") is not None else MockCompletionResponse(choices=None, tool_calls_response=None))

    return mock


@pytest.fixture
def sample_accounts_list_fixture():
    """Sample response for list_accounts tool."""
    return {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "name": "default",
        "cash_balance": "100000.0000",
    }


@pytest.fixture
def sample_account_detail_fixture():
    """Sample response for get_account tool."""
    return {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "name": "default",
        "cash_balance": "100000.0000",
        "equity": "100000.0000",
        "open_positions": 0,
        "open_orders": 0,
    }


@pytest.fixture
def sample_place_order_result_fixture():
    """Sample response for place_order tool."""
    return {
        "id": "order-uuid-123",
        "symbol": "AAPL",
        "side": "BUY",
        "order_type": "MARKET",
        "quantity": "10",
        "status": "FILLED",
        "filled_quantity": "10",
    }


@pytest.fixture
def sample_orders_list_fixture():
    """Sample response for list_orders tool."""
    return [
        {
            "id": "order-uuid-123",
            "symbol": "AAPL",
            "side": "BUY",
            "order_type": "MARKET",
            "quantity": "10",
            "status": "FILLED",
            "filled_quantity": "10",
        }
    ]


@pytest.fixture
def sample_cancel_order_result_fixture():
    """Sample response for cancel_order tool."""
    return {
        "id": "order-uuid-123",
        "status": "CANCELLED",
    }


@pytest.fixture
def error_response_fixture():
    """Sample error response for failed tool execution."""
    return {"error": "Insufficient funds to place order"}


# ===== fd-quant compute plane (backtest-engine-v2, design D2) =====


@pytest.fixture(scope="session")
def compute_plane_server():
    """Start the **real** fd-quant compute plane and point the client at it.

    Why the real server rather than a stub: design D2 makes "same numbers as the
    compute plane" a spec-level acceptance item, so the test must exercise the
    actual cross-repo contract — a stub would pass while the contract was broken.
    Starting it costs one process for the whole session; the server is a stateless
    pure-compute service with no credentials, so there is nothing to isolate.

    Skips (rather than fails) when the fd-quant checkout is absent, so this repo's
    suite stays runnable on a machine that only has fd-paper-trading.
    """
    import os
    import socket
    import subprocess
    import time
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    candidate = Path(os.environ.get("FD_QUANT_REPO", repo.parent / "fd-quant"))
    if not (candidate / "fd_quant" / "compute" / "server.py").exists():
        pytest.skip(f"fd-quant checkout not found at {candidate} (set FD_QUANT_REPO)")

    python = candidate / ".venv" / "bin" / "python"
    if not python.exists():
        python = Path("python3")

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    proc = subprocess.Popen(
        [str(python), "-m", "fd_quant.compute.server", "--http", str(port)],
        cwd=str(candidate), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    deadline = time.time() + 30
    ready = False
    while time.time() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"compute plane exited early:\n{out[-2000:]}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                ready = True
                break
        except OSError:
            time.sleep(0.2)
    if not ready:
        proc.kill()
        raise RuntimeError(f"compute plane did not open port {port} within 30s")

    previous = os.environ.get("FD_QUANT_COMPUTE_ENDPOINT")
    os.environ["FD_QUANT_COMPUTE_ENDPOINT"] = url
    try:
        yield url
    finally:
        if previous is None:
            os.environ.pop("FD_QUANT_COMPUTE_ENDPOINT", None)
        else:
            os.environ["FD_QUANT_COMPUTE_ENDPOINT"] = previous
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="session", autouse=True)
def _compute_plane_for_backtests(request):
    """Session-scoped autouse wrapper: start the compute plane for every test.

    Autouse rather than name-matched, because ``item.fixturenames.append`` in
    ``pytest_collection_modifyitems`` runs after fixture resolution and therefore
    never actually requests the fixture. Starting one extra local process for the
    whole session is cheaper than a hook that silently does nothing.

    Tests that do not touch the compute plane pay nothing but process start-up;
    the server is idle otherwise.
    """
    from pathlib import Path
    import os, socket, subprocess, time

    repo = Path(__file__).resolve().parents[1]
    candidate = Path(os.environ.get("FD_QUANT_REPO", repo.parent / "fd-quant"))
    server = candidate / "fd_quant" / "compute" / "server.py"
    if not server.exists():
        # 不 skip：fd-paper-trading 的回测路径**依赖**计算面，静默跳过会让
        # "跨仓口径一致"这条 spec 验收在 CI 里变成空跑。
        pytest.fail(
            f"fd-quant compute plane not found at {candidate} — the backtest path "
            f"requires it (set FD_QUANT_REPO to the checkout)"
        )

    python = candidate / ".venv" / "bin" / "python"
    if not python.exists():
        python = Path("python3")

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    proc = subprocess.Popen(
        [str(python), "-m", "fd_quant.compute.server", "--http", str(port)],
        cwd=str(candidate), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    deadline = time.time() + 30
    ready = False
    while time.time() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"compute plane exited early:\n{out[-2000:]}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                ready = True
                break
        except OSError:
            time.sleep(0.2)
    if not ready:
        proc.kill()
        raise RuntimeError(f"compute plane did not open port {port} within 30s")

    previous = os.environ.get("FD_QUANT_COMPUTE_ENDPOINT")
    os.environ["FD_QUANT_COMPUTE_ENDPOINT"] = f"http://127.0.0.1:{port}/mcp"
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        if previous is None:
            os.environ.pop("FD_QUANT_COMPUTE_ENDPOINT", None)
        else:
            os.environ["FD_QUANT_COMPUTE_ENDPOINT"] = previous
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
