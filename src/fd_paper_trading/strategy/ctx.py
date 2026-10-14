"""The strategy evaluation contract: ``on_bar(ctx)``.

One ``StrategyContext`` is built per bar (backtest or live tick) and handed
to the strategy — a template interpreter or custom code. It exposes bar
history (oldest-first, ending at the current bar), indicators computed over
that history, account state, and order emitters that collect ``Signal``s.

Nothing here executes anything: the backtester (fill-at-close simulation) and
the live runner (orders via PaperExecutionService) each consume the collected
signals their own way — that is what keeps backtest and live in parity.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..marketdata.provider import Bar
from . import indicators


@dataclass(frozen=True)
class Signal:
    """An order intention emitted by a strategy via ``ctx.buy``/``ctx.sell``."""

    side: str  # "BUY" | "SELL"
    quantity: Decimal
    limit_price: Decimal | None = None  # None -> MARKET


class StrategyContext:
    """Per-bar view given to a strategy.

    ``bars`` must be oldest-first and INCLUDE the current (signal) bar as the
    last element. ``position``/``cash`` are the account state *before* any
    fill this bar's signals may produce.
    """

    def __init__(self, bars: list[Bar], position: Decimal, cash: Decimal) -> None:
        if not bars:
            raise ValueError("StrategyContext requires at least the current bar")
        self._bars = bars
        self.position = position
        self.cash = cash
        self.signals: list[Signal] = []
        self._closes: list[float] | None = None

    # ---- bar history (oldest-first, ending at the current bar) ----

    def closes(self) -> list[float]:
        if self._closes is None:
            self._closes = [float(b.close) for b in self._bars]
        return self._closes

    def opens(self) -> list[float]:
        return [float(b.open) for b in self._bars]

    def highs(self) -> list[float]:
        return [float(b.high) for b in self._bars]

    def lows(self) -> list[float]:
        return [float(b.low) for b in self._bars]

    def volumes(self) -> list[float]:
        return [float(b.volume) for b in self._bars]

    # ---- indicators over the close history (None until enough bars) ----

    def sma(self, n: int) -> float | None:
        return indicators.sma(self.closes(), n)

    def ema(self, n: int) -> float | None:
        return indicators.ema(self.closes(), n)

    def rsi(self, n: int = 14) -> float | None:
        return indicators.rsi(self.closes(), n)

    def macd(
        self, fast: int = 12, slow: int = 26, signal: int = 9
    ) -> tuple[float, float, float] | None:
        return indicators.macd(self.closes(), fast, slow, signal)

    def bollinger(self, n: int = 20, k: float = 2.0) -> tuple[float, float, float] | None:
        return indicators.bollinger(self.closes(), n, k)

    # ---- order emitters ----

    def buy(self, qty: int | float | Decimal, limit_price: float | Decimal | None = None) -> None:
        self._emit("BUY", qty, limit_price)

    def sell(self, qty: int | float | Decimal, limit_price: float | Decimal | None = None) -> None:
        self._emit("SELL", qty, limit_price)

    def _emit(self, side: str, qty: int | float | Decimal, limit_price: float | Decimal | None) -> None:
        quantity = Decimal(str(qty))
        if quantity <= 0:
            raise ValueError(f"{side} quantity must be positive, got {qty!r}")
        limit = None
        if limit_price is not None:
            limit = Decimal(str(limit_price))
            if limit <= 0:
                raise ValueError(f"{side} limit_price must be positive, got {limit_price!r}")
        self.signals.append(Signal(side=side, quantity=quantity, limit_price=limit))
