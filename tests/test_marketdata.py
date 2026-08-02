"""SimulatedMarketDataProvider: search and bars (task 2.5)."""

from decimal import Decimal

from fd_trade.marketdata import SimulatedMarketDataProvider
from fd_trade.marketdata.provider import SUPPORTED_TIMEFRAMES


async def test_search_prefix_matches_symbol_or_name():
    provider = SimulatedMarketDataProvider()
    results = await provider.search_instruments("AAP")
    symbols = {r.symbol for r in results}
    assert "AAPL" in symbols
    for r in results:
        assert r.venue == "SIM"
        assert r.symbol and r.name


async def test_search_is_case_insensitive_and_matches_name():
    provider = SimulatedMarketDataProvider()
    results = await provider.search_instruments("micro")
    assert any(r.symbol == "MSFT" for r in results)


async def test_search_empty_query_returns_empty():
    provider = SimulatedMarketDataProvider()
    assert await provider.search_instruments("") == []
    assert await provider.search_instruments("   ") == []


async def test_search_no_match_returns_empty():
    provider = SimulatedMarketDataProvider()
    assert await provider.search_instruments("ZZZZZ") == []


async def test_bars_count_capped_at_limit():
    provider = SimulatedMarketDataProvider()
    bars = await provider.get_bars("AAPL", "1m", 50)
    assert len(bars) == 50


async def test_bars_ordered_oldest_first():
    provider = SimulatedMarketDataProvider()
    bars = await provider.get_bars("AAPL", "5m", 30)
    timestamps = [b.ts for b in bars]
    assert timestamps == sorted(timestamps)
    # Adjacent bars are one timeframe step apart.
    step = SUPPORTED_TIMEFRAMES["5m"]
    assert all(
        bars[i + 1].ts - bars[i].ts == step for i in range(len(bars) - 1)
    )


async def test_bars_are_deterministic_per_symbol_and_timeframe():
    provider = SimulatedMarketDataProvider()
    first = await provider.get_bars("AAPL", "1m", 20)
    second = await provider.get_bars("AAPL", "1m", 20)
    assert [(b.open, b.high, b.low, b.close, b.volume) for b in first] == [
        (b.open, b.high, b.low, b.close, b.volume) for b in second
    ]


async def test_bars_high_low_span_open_close():
    provider = SimulatedMarketDataProvider()
    bars = await provider.get_bars("MSFT", "1h", 10)
    for b in bars:
        assert b.high >= max(b.open, b.close)
        assert b.low <= min(b.open, b.close)
        assert b.volume > 0
        assert isinstance(b.close, Decimal)
