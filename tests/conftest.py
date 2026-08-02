"""Shared test fixtures and helpers.

Tests run against an in-memory SQLite database via ``httpx``'s ASGI transport,
so no external Postgres or broker is required.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from fd_trade.api.app import create_app
from fd_trade.marketdata import SimulatedMarketDataProvider
from fd_trade.persistence.base import init_db

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
