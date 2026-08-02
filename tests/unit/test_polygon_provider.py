"""Regression test: PolygonMarketDataProvider implements search and bars.

It previously inherited ``raise NotImplementedError`` from the base
``MarketDataProvider`` for ``search_instruments`` and ``get_bars``, so the
search and bars endpoints returned HTTP 500 when ``MARKET_DATA_PROVIDER=polygon``
(the default local config). Quotes still come from Polygon; search/bars fall
back to the deterministic simulated universe (no network).
"""

import pytest

from fd_trade.marketdata import PolygonMarketDataProvider


@pytest.fixture
async def provider():
    p = PolygonMarketDataProvider(api_key="fake-key")
    yield p
    await p.aclose()


async def test_polygon_search_returns_results(provider):
    results = await provider.search_instruments("AAP")
    assert any(r.symbol == "AAPL" for r in results)
    assert all(r.venue == "SIM" for r in results)


async def test_polygon_search_empty_query_returns_empty(provider):
    assert await provider.search_instruments("") == []


async def test_polygon_get_bars_returns_oldest_first(provider):
    bars = await provider.get_bars("AAPL", "1m", 20)
    assert len(bars) == 20
    timestamps = [b.ts for b in bars]
    assert timestamps == sorted(timestamps)


async def test_polygon_get_instrument_returns_equity(provider):
    assert provider.get_instrument("AAPL") is not None
    assert provider.get_instrument("UNKNOWN-SYMBOL-XYZ") is not None  # test-kit always returns one
