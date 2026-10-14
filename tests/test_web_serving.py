"""SPA static serving and route precedence (task 5.3)."""

from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from fd_paper_trading.api.app import create_app
from fd_paper_trading.marketdata import SimulatedMarketDataProvider
from fd_paper_trading.persistence.base import init_db

TEST_DB_URL = "sqlite+aiosqlite://"


@pytest_asyncio.fixture
async def web_app(tmp_path: Path):
    (tmp_path / "index.html").write_text("<html>SPA</html>")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('app');")
    application = create_app(
        database_url=TEST_DB_URL,
        market_data_provider=SimulatedMarketDataProvider(),
        web_dist_dir=tmp_path,
    )
    await init_db(application.state.db)
    yield application
    await application.state.db.engine.dispose()


@pytest_asyncio.fixture
async def web_client(web_app):
    transport = ASGITransport(app=web_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_deep_link_serves_index_html(web_client):
    r = await web_client.get("/accounts/123/orders")
    assert r.status_code == 200
    assert "SPA" in r.text


async def test_static_asset_is_served(web_client):
    r = await web_client.get("/assets/app.js")
    assert r.status_code == 200
    assert "console.log" in r.text


async def test_api_routes_not_shadowed_by_spa(web_client):
    # No auth -> 401 from the API, not the SPA fallback.
    r = await web_client.get("/api/v1/instruments/search?q=AAP")
    assert r.status_code == 401


async def test_unknown_api_path_returns_404_not_index(web_client):
    r = await web_client.get("/api/v1/does-not-exist")
    assert r.status_code == 404


async def test_health_still_works(web_client):
    r = await web_client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_absent_web_dist_returns_404_for_deep_link():
    # An app with no built UI (non-existent dist) does not mount the SPA, so a
    # deep link 404s instead of serving index.html.
    application = create_app(
        database_url=TEST_DB_URL,
        market_data_provider=SimulatedMarketDataProvider(),
        web_dist_dir=Path("/nonexistent-fd-paper-trading-web-dist"),
    )
    await init_db(application.state.db)
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/accounts/123/orders")
        assert r.status_code == 404
    await application.state.db.engine.dispose()
