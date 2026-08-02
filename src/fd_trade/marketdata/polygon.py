"""Polygon.io market-data provider.

Free-tier note: the free Polygon plan is NOT entitled to real-time quotes
(NBBO / last-trade / snapshot all return 403). It IS entitled to the previous
trading day's OHLC aggregate, so this provider uses the last close as the
reference price and synthesizes a small spread around it. Prices therefore
reflect the last completed trading day, not real-time intraday quotes. Upgrade
to a paid plan and swap in a real NBBO call to get live bid/ask.
"""

import time
from decimal import Decimal

import httpx
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from .provider import MarketDataProvider, Quote
from .simulated import SIM_VENUE, SimulatedMarketDataProvider

# Half-spread as a fraction of price (5 bps each side => 10 bps total),
# with a $0.01 floor so bid != ask even for very low-priced stocks.
_HALF_SPREAD_BPS = Decimal("0.0005")
_MIN_HALF_SPREAD = Decimal("0.01")
_PRICE_Q = Decimal("0.01")  # equity price precision (cents)


class PolygonMarketDataProvider(MarketDataProvider):
    """Fetches the previous close from Polygon and derives a quote around it."""

    BASE_URL = "https://api.polygon.io/v2/aggs/ticker"

    def __init__(self, api_key: str, cache_ttl_seconds: float = 300.0) -> None:
        self.api_key = api_key
        self.cache_ttl = cache_ttl_seconds
        self._cache: dict[str, tuple[float, Quote]] = {}
        self._client = httpx.AsyncClient(timeout=10.0)
        # The Polygon free tier exposes no instrument-search or bars endpoints,
        # so those fall back to the deterministic simulated universe/bars. This
        # keeps the UI's symbol search and charts working with real quotes.
        self._sim = SimulatedMarketDataProvider()

    async def search_instruments(self, q: str) -> list:
        return await self._sim.search_instruments(q)

    async def get_bars(self, symbol: str, timeframe: str, limit: int) -> list:
        return await self._sim.get_bars(symbol, timeframe, limit)

    async def get_quote(self, symbol: str) -> Quote | None:
        if not self.api_key:
            return None
        now = time.monotonic()
        cached = self._cache.get(symbol)
        if cached and now - cached[0] < self.cache_ttl:
            return cached[1]
        quote = await self._fetch_quote(symbol)
        if quote is not None:
            self._cache[symbol] = (now, quote)
        return quote

    async def _fetch_quote(self, symbol: str) -> Quote | None:
        url = f"{self.BASE_URL}/{symbol}/prev"
        try:
            resp = await self._client.get(url, params={"apiKey": self.api_key})
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None  # rate limited, not entitled, unknown symbol, etc.

        data = resp.json()
        results = data.get("results") or []
        if not results:
            return None

        close = results[0].get("c")
        if close is None:
            return None
        close = Decimal(str(close))
        if close <= 0:
            return None

        half = (close * _HALF_SPREAD_BPS).quantize(_PRICE_Q)
        if half < _MIN_HALF_SPREAD:
            half = _MIN_HALF_SPREAD
        bid = (close - half).quantize(_PRICE_Q)
        ask = (close + half).quantize(_PRICE_Q)
        return Quote(instrument_id=f"{symbol}.{SIM_VENUE}", bid=bid, ask=ask)

    def get_instrument(self, symbol: str) -> Instrument | None:
        return TestInstrumentProvider.equity(symbol=symbol, venue=SIM_VENUE)

    async def aclose(self) -> None:
        await self._client.aclose()
