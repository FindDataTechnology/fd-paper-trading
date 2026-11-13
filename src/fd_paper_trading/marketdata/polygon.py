"""Polygon.io market-data provider.

Free-tier note: the free Polygon plan is NOT entitled to real-time quotes
(NBBO / last-trade / snapshot all return 403). It IS entitled to the previous
trading day's OHLC aggregate, so this provider uses the last close as the
reference price and synthesizes a small spread around it. Prices therefore
reflect the last completed trading day, not real-time intraday quotes. Upgrade
to a paid plan and swap in a real NBBO call to get live bid/ask.
"""

from __future__ import annotations

import time
import logging
from datetime import date, timedelta
from decimal import Decimal
import httpx

from .provider import Bar, Instrument, MarketDataProvider, Quote
from .simulated import SIM_VENUE, SimulatedMarketDataProvider

logger = logging.getLogger(__name__)

# Half-spread as a fraction of price (5 bps each side => 10 bps total),
# with a $0.01 floor so bid != ask even for very low-priced stocks.
_HALF_SPREAD_BPS = Decimal("0.0005")
_MIN_HALF_SPREAD = Decimal("0.01")
_PRICE_Q = Decimal("0.01")  # equity price precision (cents)


class PolygonMarketDataProvider(MarketDataProvider):
    """Previous-close quotes and daily bars, both from Polygon's own endpoints."""

    BASE_URL = "https://api.polygon.io/v2/aggs/ticker"

    #: 只有日线：免费版聚合端点按天返回，更细的粒度要付费套餐。
    supported_timeframes: tuple[str, ...] = ("1d",)

    def __init__(self, api_key: str, cache_ttl_seconds: float = 300.0) -> None:
        self.api_key = api_key
        self.cache_ttl = cache_ttl_seconds
        self._cache: dict[str, tuple[float, Quote]] = {}
        self._client = httpx.AsyncClient(timeout=10.0)
        # 免费版的**搜索/合约元数据**没有端点，这两项代理给确定性模拟盘是允许的：
        # 它们是元数据，不是价格（规格「一个价格世界」管的是价格序列）。
        # 价格（bar / quote）一律走 Polygon 自己的端点——见 get_bars 的说明。
        self._sim = SimulatedMarketDataProvider()

    async def search_instruments(self, q: str) -> list:
        return await self._sim.search_instruments(q)

    async def get_bars(self, symbol: str, timeframe: str, limit: int) -> list[Bar]:
        """Daily bars from Polygon's own aggregate endpoint, oldest-first.

        这里原先代理给 ``SimulatedMarketDataProvider``（2026-07-17 的一个修复，
        理由是"免费版没有 bars 端点"）。那个理由**不成立**：
        ``/v2/aggs/ticker/{t}/range/1/day`` 在免费版上是通的（2026-11-13 实测
        AAPL 取回 7 根真 bar，``status=OK``）。

        而在规格「One price world for backtest, live, and manual fills」之下，
        这个代理不只是浪费——它让**同一个 provider 实例**的 quote 来自 Polygon、
        bars 来自合成随机游走：回测的信号算在假序列上，成交价却是真的，
        两边都看不出异常。所以宁可在拿不到时返回空（数据缺口；回测路径会明确报
        ``no bar data for symbol …``），也不拿另一套序列顶替。
        """
        if timeframe not in self.supported_timeframes:
            raise ValueError(
                f"polygon provider serves {list(self.supported_timeframes)} only, "
                f"not {timeframe!r}"
            )
        if not self.api_key:
            return []
        # 目标是"最近 limit 个交易日"：按日历多要一些天，跨过周末与休市。
        end = date.today()
        start = end - timedelta(days=max(2 * limit, limit + 10))
        url = f"{self.BASE_URL}/{symbol.upper()}/range/1/day/{start}/{end}"
        try:
            resp = await self._client.get(
                url,
                params={
                    "apiKey": self.api_key,
                    "adjusted": "true",
                    "sort": "desc",
                    "limit": limit,
                },
            )
        except httpx.HTTPError as exc:
            logger.warning("polygon bars: %s for %s: %s", type(exc).__name__, symbol, exc)
            return []
        if resp.status_code != 200:
            # 403/429 是免费版的常态（未授权/限流），404 是不认识的标的：
            # 都不是"有数据但没说"，一律按缺口处理，绝不换成模拟序列。
            logger.warning(
                "polygon bars: HTTP %s for %s (no substitution)", resp.status_code, symbol
            )
            return []
        results = resp.json().get("results") or []
        bars: list[Bar] = []
        for row in reversed(results):  # desc → oldest-first
            close = row.get("c")
            if close is None:
                continue
            close = Decimal(str(close))
            bars.append(
                Bar(
                    ts=int(row["t"]) // 1000,  # Polygon 是毫秒
                    open=Decimal(str(row.get("o", close))),
                    high=Decimal(str(row.get("h", close))),
                    low=Decimal(str(row.get("l", close))),
                    close=close,
                    volume=Decimal(int(row.get("v") or 0)),
                )
            )
        return bars

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
        # Same SIM equity universe as the simulated provider (cached there).
        return self._sim.get_instrument(symbol)

    async def aclose(self) -> None:
        await self._client.aclose()
