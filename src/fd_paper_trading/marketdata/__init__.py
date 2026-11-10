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
    "AstockMarketDataProvider": ".astock",
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
        from .astock import AstockMarketDataProvider

        def build_market_data_provider(settings: Settings) -> MarketDataProvider:
            """Select a market-data provider through the source registry.

            Selection logic and the whole deprecation window live in
            ``selection.py``: the registry says which *access protocol* a source
            uses, and this function maps that protocol onto a provider class.
            No source name appears here (change task 3.3) — swapping a source is
            a declarations edit, not a code edit.
            """
            from .selection import (
                PROVIDER_FD_OPEN_DATA,
                PROVIDER_FEDERATED,
                PROVIDER_POLYGON,
                PROVIDER_SIMULATED,
                SourceSelectionError,
                resolve_selection,
            )

            sel = resolve_selection(settings)

            if sel.provider == PROVIDER_FEDERATED:
                return AstockMarketDataProvider(
                    dsn=sel.dsn,
                    source=sel.pinned_source,
                    calendar_refs=sel.calendar_refs or settings.astock_calendar_refs,
                    # 注册表 id 传进去，provider 才能报出"这个源的水位到哪天"
                    # （task 6.5）。旧路径（无 MARKET_DATA_SOURCE_ID）为 None，
                    # provider 就不提供水位，判据探针退回固定窗口。
                    registry_source_id=sel.source_id,
                )
            if sel.provider == PROVIDER_FD_OPEN_DATA:
                return FdOpenDataMarketDataProvider()
            if sel.provider == PROVIDER_POLYGON:
                return PolygonMarketDataProvider(api_key=settings.market_data_api_key)
            if sel.provider == PROVIDER_SIMULATED:
                return SimulatedMarketDataProvider()
            raise SourceSelectionError(
                f"selection produced unknown provider {sel.provider!r}"
            )

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
    "AstockMarketDataProvider",
    "SIM_VENUE",
    "build_market_data_provider",
]
