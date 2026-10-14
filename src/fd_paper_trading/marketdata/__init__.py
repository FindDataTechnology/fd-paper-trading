"""Market-data ingestion.

A provider supplies quotes and instruments for symbols. Implementations:
- ``SimulatedMarketDataProvider``: deterministic synthetic quotes (tests/dev).
- ``PolygonMarketDataProvider``: real end-of-day prices from Polygon.io (free tier).
- ``FdOpenDataMarketDataProvider``: real daily bars from the fd-open-data-mcp
  ontology (akshare/yfinance/etc. with a read-through cache).

Heavy provider implementations (which pull in httpx / the fd-open-data client)
are imported lazily via ``__getattr__`` (PEP 562). This keeps ``from
..marketdata.provider import Bar`` cheap - important for the custom-code
sandbox, which spawns one subprocess per bar and must not pay the httpx import
cost on every spawn.
"""

from .provider import (
    DEFAULT_BAR_LIMIT,
    MAX_BAR_LIMIT,
    SUPPORTED_TIMEFRAMES,
    Bar,
    InstrumentSearchResult,
    MarketDataProvider,
    Quote,
)

# Note: ``Settings`` is intentionally NOT imported eagerly here. ``config`` pulls
# in ``pydantic_settings`` (~100 ms), which the custom-code sandbox subprocess
# must not pay on every per-bar spawn. Settings is imported lazily inside
# ``build_market_data_provider`` below.

# Names that require importing a heavy provider module. Resolved on first
# access via __getattr__ and then cached on this module for subsequent lookups.
_LAZY = {
    "SimulatedMarketDataProvider": ".simulated",
    "SIM_VENUE": ".simulated",
    "PolygonMarketDataProvider": ".polygon",
    "FdOpenDataMarketDataProvider": ".fd_open_data",
}


def __getattr__(name: str):
    submodule = _LAZY.get(name)
    if submodule is not None:
        import importlib

        mod = importlib.import_module(submodule, __name__)
        value = getattr(mod, name)
        globals()[name] = value  # cache for subsequent direct lookups
        return value
    if name == "build_market_data_provider":
        from ..config import Settings
        from .simulated import SimulatedMarketDataProvider
        from .polygon import PolygonMarketDataProvider
        from .fd_open_data import FdOpenDataMarketDataProvider

        def build_market_data_provider(settings: Settings) -> MarketDataProvider:
            """Select a market-data provider from ``settings``.

            ``fd-open-data`` -> ``FdOpenDataMarketDataProvider``; ``polygon``
            (with an API key) -> ``PolygonMarketDataProvider``; any other value
            (including ``simulated`` and unset/unknown) ->
            ``SimulatedMarketDataProvider``. This is the single home for the
            fallthrough rule in the ``market-data`` spec.
            """
            if settings.market_data_provider == "fd-open-data":
                return FdOpenDataMarketDataProvider()
            if settings.market_data_provider == "polygon":
                return PolygonMarketDataProvider(api_key=settings.market_data_api_key)
            return SimulatedMarketDataProvider()

        globals()[name] = build_market_data_provider
        return build_market_data_provider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + list(_LAZY) + ["build_market_data_provider"])


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
    "FdOpenDataMarketDataProvider",
    "SIM_VENUE",
    "build_market_data_provider",
]
