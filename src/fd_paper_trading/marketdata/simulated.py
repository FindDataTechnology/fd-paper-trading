"""Deterministic simulated market-data provider for tests and local dev."""

from __future__ import annotations

import hashlib
import random
import re
import time
from decimal import Decimal
from functools import lru_cache

from .provider import (
    Bar,
    DEFAULT_BAR_LIMIT,
    Instrument,
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

#: 什么样子的字符串算"本 provider 认识的标的"。
#:
#: 规格 market-data「Instrument resolution」要求 `get_instrument` 对**不认识的**标的
#: 返回 None，而这也是下单路径 `unknown_instrument` 那道门的唯一前提
#: （`execution/service.py`: `instrument is None → OrderRejected`）。
#:
#: 2026-11-13 实测：这个 provider 原先对**任意字符串**都返回合约——连
#: `UNKNOWN-SYMBOL-XYZ` 都返回（当时那条测试还把这行为写进断言，注释是
#: "test-kit always returns one"）。后果是默认配置（没有 MARKET_DATA_SOURCE_ID
#: ⇒ 模拟源）下 `unknown_instrument` **永远打不开**：打错一个字母也能下单，
#: 只有 `no_quote` 在后面挡，而模拟源的 quote 同样对任意字符串都有。
#: 于是"两道门"在开发/本地其实是一道，且那一道也不看标的存不存在。
#:
#: 判据刻意宽松——只认**形状**，不认名单：模拟源本来就没有证券主数据，
#: 要求它记住哪些 ticker 真实存在是把测试替身当数据源用。
_SYMBOL_RE = re.compile(
    r"^(?:[A-Z]{1,5}(?:\.[A-Z]{1,2})?|\d{6}(?:\.(?:SH|SS|SZ|SHE))?)$", re.I
)


def is_known_symbol(symbol: str) -> bool:
    """True when `symbol` has a shape this provider can claim to know."""
    return bool(_SYMBOL_RE.match((symbol or "").strip()))


class SimulatedMarketDataProvider(MarketDataProvider):
    """Returns a stable, deterministic quote per symbol (no external feed).

    The base price is derived from the symbol so tests are reproducible; bid/ask
    straddle it. Instruments are this service's own value type on the ``SIM`` venue.
    ``search_instruments`` filters a static universe; ``get_bars`` synthesizes a
    deterministic, symbol-seeded OHLCV series so charts render reproducibly.
    """

    async def get_quote(self, symbol: str) -> Quote | None:
        if not is_known_symbol(symbol):
            return None  # 不认识的标的没有报价：data gap，不是编一个价
        base = self._base_price(symbol)
        return Quote(
            instrument_id=f"{symbol}.{SIM_VENUE}",
            bid=base - Decimal("0.05"),
            ask=base + Decimal("0.05"),
        )

    @staticmethod
    @lru_cache(maxsize=256)
    def _instrument(symbol: str) -> Instrument:
        # Instruments sit on the order hot path; building one per call is waste.
        name = next(
            (n for s, n in _INSTRUMENT_UNIVERSE if s == symbol.upper()), symbol.upper()
        )
        return Instrument(
            symbol=symbol.upper(),
            symbol_id=f"{symbol.upper()}.{SIM_VENUE}",
            venue=SIM_VENUE,
            name=name,
        )

    def get_instrument(self, symbol: str) -> Instrument | None:
        if not is_known_symbol(symbol):
            return None  # 规格：不认识的标的返回 None（下单门的前提）
        return self._instrument(symbol)

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
        if not is_known_symbol(symbol):
            # 同一个世界：不认识的标的没有 quote，也不该有 bar。
            # 否则回测能在一根根合成 K 线上跑出曲线，下单却被 unknown_instrument 拒。
            return []
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
