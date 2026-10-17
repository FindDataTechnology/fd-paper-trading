"""The strategy evaluation contract: ``on_bar(ctx)`` and ``target_weights(ctx)``.

A ``StrategyContext`` is built per bar (backtest or live tick) and handed to the
strategy — a template interpreter or custom code. It exposes bar history
(oldest-first, ending at the current bar), indicators computed over that
history, account state, and order emitters that collect ``Signal``s.

Nothing here executes anything: the single-symbol backtester (fill-at-close) and
the live runner (orders via PaperExecutionService) each consume the collected
signals their own way — that is what keeps backtest and live in parity. The
weight track differs in what a strategy *returns* (a target-weight mapping rather
than orders), so it is served by ``WeightContext``, which reuses this class for
the per-symbol series access and adds portfolio-level access.

Two context classes, one discipline: neither may look ahead. ``bars`` always ends
at the bar currently being decided, and ``WeightContext`` builds each symbol's
series with the same cutoff, so ``ctx.closes(sym)`` can never include a date the
portfolio-valuation step has not reached yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Mapping, Sequence

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


class SymbolSeries:
    """One symbol's bar history, oldest-first, ending at the decision bar.

    A missing bar is *not* forward-filled here: ``latest_close`` looks back to the
    most recent available close (the valuation rule for a halted symbol), while
    ``has_bar_on`` reports whether the decision date itself traded — the two
    questions the weight track must answer differently (value on the last close,
    but do not pretend it was tradable).
    """

    def __init__(self, symbol: str, bars: list[Bar]) -> None:
        self.symbol = symbol
        self._bars = bars
        self._closes: list[float] | None = None

    @property
    def bars(self) -> list[Bar]:
        return list(self._bars)

    def closes(self) -> list[float]:
        if self._closes is None:
            self._closes = [float(b.close) for b in self._bars]
        return self._closes

    def latest_close(self) -> float | None:
        """Most recent available close, or None when the symbol never traded."""
        return float(self._bars[-1].close) if self._bars else None

    def latest_bar_date(self) -> date | None:
        if not self._bars:
            return None
        return datetime.fromtimestamp(self._bars[-1].ts, tz=timezone.utc).date()

    def has_bar_on(self, day: date) -> bool:
        return self.latest_bar_date() == day

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


class WeightContext:
    """Per-rebalance view given to a ``target_weights(ctx)`` strategy.

    Series access is per symbol (``ctx.closes(sym)``), scoped to the universe:
    asking for a symbol outside it raises, rather than silently returning an
    empty series that a strategy could mistake for "no data yet". Each series
    ends at the decision date, so the strategy sees exactly what a portfolio
    manager would have seen at that close.

    ``current_weights()`` reports the portfolio's weights at the latest
    valuation, cash included, so a strategy can reason about turnover without
    being handed the raw ledger.
    """

    def __init__(
        self,
        day: date,
        universe: Sequence[str],
        series: Mapping[str, SymbolSeries],
        cash: Decimal,
        positions: Mapping[str, Decimal],
        cash_floor: float = 0.0,
    ) -> None:
        if not universe:
            raise ValueError("WeightContext requires a non-empty universe")
        self._day = day
        self._universe = list(universe)
        self._series = dict(series)
        self.cash = cash
        self._positions = {k: Decimal(v) for k, v in positions.items()}
        self.cash_floor = cash_floor

    # ---- portfolio-level access ----

    @property
    def date(self) -> date:
        return self._day

    @property
    def universe(self) -> list[str]:
        return list(self._universe)

    def series(self, symbol: str) -> SymbolSeries:
        """The named symbol's series; a symbol outside the universe raises."""
        key = str(symbol).strip().upper()
        if key not in self._universe:
            raise KeyError(
                f"symbol {symbol!r} is not in the universe {self._universe}"
            )
        found = self._series.get(key)
        if found is None:
            # Reachable only if the caller built the context with a partial
            # mapping; report it as missing data, never as an empty series.
            raise KeyError(f"no series loaded for symbol {key!r}")
        return found

    def closes(self, symbol: str) -> list[float]:
        return self.series(symbol).closes()

    def latest_close(self, symbol: str) -> float | None:
        return self.series(symbol).latest_close()

    def sma(self, symbol: str, n: int) -> float | None:
        return self.series(symbol).sma(n)

    def ema(self, symbol: str, n: int) -> float | None:
        return self.series(symbol).ema(n)

    def rsi(self, symbol: str, n: int = 14) -> float | None:
        return self.series(symbol).rsi(n)

    def macd(
        self, symbol: str, fast: int = 12, slow: int = 26, signal: int = 9
    ) -> tuple[float, float, float] | None:
        return self.series(symbol).macd(fast, slow, signal)

    def bollinger(
        self, symbol: str, n: int = 20, k: float = 2.0
    ) -> tuple[float, float, float] | None:
        return self.series(symbol).bollinger(n, k)

    def position(self, symbol: str) -> Decimal:
        return self._positions.get(str(symbol).strip().upper(), Decimal(0))

    def market_value(self, symbol: str) -> Decimal:
        """Position value at the latest available close (0 when not held)."""
        qty = self.position(symbol)
        if qty == 0:
            return Decimal(0)
        close = self.latest_close(symbol)
        if close is None:
            return Decimal(0)
        return qty * Decimal(str(close))

    def equity(self) -> Decimal:
        return self.cash + sum(
            (self.market_value(sym) for sym in self._universe), Decimal(0)
        )

    def current_weights(self) -> dict[str, float]:
        """Current weights over the universe at the latest closes, cash included.

        Cash is reported under ``"CASH"`` so the mapping sums to 1 whenever the
        portfolio has value; an empty portfolio (no cash, no positions) returns
        ``{}`` rather than a set of zeros that could read as a real target.
        """
        equity = self.equity()
        if equity <= 0:
            return {}
        out = {
            sym: float(self.market_value(sym) / equity) for sym in self._universe
        }
        out["CASH"] = float(self.cash / equity)
        return out
