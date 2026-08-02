"""Deterministic simulated market-data provider for tests and local dev."""

import hashlib
import random
import time
from decimal import Decimal

from nautilus_trader.model.instruments import Instrument
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from .provider import (
    Bar,
    DEFAULT_BAR_LIMIT,
    MarketDataProvider,
    Quote,
    SUPPORTED_TIMEFRAMES,
)

SIM_VENUE = "SIM"

# Curated static universe for the search endpoint. The simulated provider has
# no external directory, so search filters this list by symbol/name.
_INSTRUMENT_UNIVERSE: list[tuple[str, str]] = [
    ("AAPL", "Apple Inc."),
    ("MSFT", "Microsoft Corp."),
    ("GOOGL", "Alphabet Inc. Class A"),
    ("GOOG", "Alphabet Inc. Class C"),
    ("AMZN", "Amazon.com Inc."),
    ("TSLA", "Tesla Inc."),
    ("META", "Meta Platforms Inc."),
    ("NVDA", "NVIDIA Corp."),
    ("NFLX", "Netflix Inc."),
    ("AMD", "Advanced Micro Devices Inc."),
    ("INTC", "Intel Corp."),
    ("ORCL", "Oracle Corp."),
    ("ADBE", "Adobe Inc."),
    ("CRM", "Salesforce Inc."),
    ("PYPL", "PayPal Holdings Inc."),
    ("DIS", "Walt Disney Co."),
    ("BABA", "Alibaba Group ADR"),
    ("JPM", "JPMorgan Chase & Co."),
    ("V", "Visa Inc."),
    ("MA", "Mastercard Inc."),
]

_PRICE_Q = Decimal("0.01")


class SimulatedMarketDataProvider(MarketDataProvider):
    """Returns a stable, deterministic quote per symbol (no external feed).

    The base price is derived from the symbol so tests are reproducible; bid/ask
    straddle it. Instruments are Nautilus ``Equity`` objects on the ``SIM`` venue.
    ``search_instruments`` filters a static universe; ``get_bars`` synthesizes a
    deterministic, symbol-seeded OHLCV series so charts render reproducibly.
    """

    async def get_quote(self, symbol: str) -> Quote | None:
        base = self._base_price(symbol)
        return Quote(
            instrument_id=f"{symbol}.{SIM_VENUE}",
            bid=base - Decimal("0.05"),
            ask=base + Decimal("0.05"),
        )

    def get_instrument(self, symbol: str) -> Instrument | None:
        return TestInstrumentProvider.equity(symbol=symbol, venue=SIM_VENUE)

    async def search_instruments(self, q: str) -> list:
        from .provider import InstrumentSearchResult

        needle = (q or "").strip().lower()
        if not needle:
            return []
        return [
            InstrumentSearchResult(symbol=sym, name=name, venue=SIM_VENUE)
            for sym, name in _INSTRUMENT_UNIVERSE
            if needle in sym.lower() or needle in name.lower()
        ]

    async def get_bars(self, symbol: str, timeframe: str, limit: int) -> list[Bar]:
        step = SUPPORTED_TIMEFRAMES.get(timeframe, 60)
        n = max(1, min(limit, DEFAULT_BAR_LIMIT * 5))  # hard cap for safety
        # Seed from symbol+timeframe so the series is stable per (symbol, tf).
        seed = int(
            hashlib.sha256(f"{symbol}|{timeframe}".encode("utf-8")).hexdigest()[:8], 16
        )
        rng = random.Random(seed)
        base = float(self._base_price(symbol))

        # Anchor the most recent bar to the current time bucketed to the step so
        # bars align to timeframe boundaries; step backwards for older bars.
        now = int(time.time())
        last_ts = now - (now % step)

        bars: list[Bar] = []
        price = base
        for i in range(n):
            ts = last_ts - (n - 1 - i) * step
            open_ = price
            close = open_ * (1 + rng.uniform(-0.012, 0.012))
            high = max(open_, close) * (1 + rng.uniform(0.0, 0.006))
            low = min(open_, close) * (1 - rng.uniform(0.0, 0.006))
            volume = Decimal(rng.randint(1_000, 60_000))
            bars.append(
                Bar(
                    ts=ts,
                    open=Decimal(str(open_)).quantize(_PRICE_Q),
                    high=Decimal(str(high)).quantize(_PRICE_Q),
                    low=Decimal(str(low)).quantize(_PRICE_Q),
                    close=Decimal(str(close)).quantize(_PRICE_Q),
                    volume=volume,
                )
            )
            price = close  # next bar opens at the previous close
        return bars

    @staticmethod
    def _base_price(symbol: str) -> Decimal:
        h = int(hashlib.sha256(symbol.encode("utf-8")).hexdigest()[:8], 16)
        return Decimal(50 + (h % 450))  # 50 .. 499
