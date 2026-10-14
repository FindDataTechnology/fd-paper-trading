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
