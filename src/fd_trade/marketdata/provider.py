"""Market-data provider interface."""

from dataclasses import dataclass
from decimal import Decimal

from nautilus_trader.model.instruments import Instrument

# Supported bar timeframes for historical OHLCV requests, mapped to their
# duration in seconds. The set is enforced by the bars endpoint.
SUPPORTED_TIMEFRAMES: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3_600,
    "1d": 86_400,
}
DEFAULT_BAR_LIMIT = 200
MAX_BAR_LIMIT = 1_000


@dataclass(frozen=True)
class Quote:
    """A top-of-book quote for a symbol."""

    instrument_id: str
    bid: Decimal
    ask: Decimal
    ts_event: int = 0


@dataclass(frozen=True)
class InstrumentSearchResult:
    """A searchable instrument hit returned by ``search_instruments``."""

    symbol: str
    name: str
    venue: str


@dataclass(frozen=True)
class Bar:
    """A single OHLCV bar. ``ts`` is a unix timestamp (seconds)."""

    ts: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


class MarketDataProvider:
    """Abstract market-data provider.

    Implementations return the latest quote for a symbol and the Nautilus
    ``Instrument`` describing it. ``get_quote`` returns ``None`` when no live
    quote is available (a data gap). ``search_instruments`` and ``get_bars``
    support the web UI's symbol search and charting.
    """

    async def get_quote(self, symbol: str) -> Quote | None:
        raise NotImplementedError

    def get_instrument(self, symbol: str) -> Instrument | None:
        raise NotImplementedError

    async def search_instruments(self, q: str) -> list[InstrumentSearchResult]:
        """Return instruments whose symbol or name match ``q``.

        Matching is case-insensitive containment. An empty or whitespace
        ``q`` returns an empty list (no expensive lookup).
        """
        raise NotImplementedError

    async def get_bars(
        self, symbol: str, timeframe: str, limit: int
    ) -> list[Bar]:
        """Return up to ``limit`` OHLCV bars for ``symbol``, oldest-first.

        ``timeframe`` must be one of ``SUPPORTED_TIMEFRAMES``; callers (the
        bars endpoint) validate this before calling.
        """
        raise NotImplementedError
