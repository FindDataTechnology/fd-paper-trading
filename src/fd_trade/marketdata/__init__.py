"""Market-data ingestion.

A provider supplies quotes and Nautilus instruments for symbols. Implementations:
- ``SimulatedMarketDataProvider``: deterministic synthetic quotes (tests/dev).
- ``PolygonMarketDataProvider``: real end-of-day prices from Polygon.io (free tier).
"""

from .polygon import PolygonMarketDataProvider
from .provider import (
    DEFAULT_BAR_LIMIT,
    MAX_BAR_LIMIT,
    SUPPORTED_TIMEFRAMES,
    Bar,
    InstrumentSearchResult,
    MarketDataProvider,
    Quote,
)
from ..config import Settings
from .simulated import SIM_VENUE, SimulatedMarketDataProvider


def build_market_data_provider(settings: Settings) -> MarketDataProvider:
    """Select a market-data provider from ``settings``.

    ``polygon`` (with an API key) -> ``PolygonMarketDataProvider``; any other
    value (including ``simulated`` and unset/unknown) -> ``SimulatedMarketDataProvider``.
    This is the single home for the fallthrough rule in the ``market-data`` spec.
    """
    if settings.market_data_provider == "polygon":
        return PolygonMarketDataProvider(api_key=settings.market_data_api_key)
    return SimulatedMarketDataProvider()


__all__ = [
    "MarketDataProvider",
    "Quote",
    "Bar",
    "InstrumentSearchResult",
    "SUPPORTED_TIMEFRAMES",
    "DEFAULT_BAR_LIMIT",
    "MAX_BAR_LIMIT",
    "SimulatedMarketDataProvider",
    "PolygonMarketDataProvider",
    "SIM_VENUE",
    "build_market_data_provider",
]
