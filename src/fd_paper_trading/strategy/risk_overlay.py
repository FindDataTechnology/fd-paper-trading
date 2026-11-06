"""Run-level risk overlay: stop-loss / trailing-stop / cooldown, shared by
the backtester and the live runner (spec: strategy-live-execution "Run-level
risk overlay shared by backtest and live").

One pure module, two consumers:

- the backtester calls ``evaluate_bar_overlay`` after each bar (bar ``low``
  reaching the stop level triggers; the fill is the worse of the stop level
  and that bar's open — gap-aware, conservative);
- the live runner calls ``evaluate_quote_overlay`` on each tick against the
  current quote.

Both return plain data (trigger + close-out instruction) so the engines keep
their own ledger semantics. ``cooldown`` state is carried by the caller (a
set of symbols with a bar-countdown) — this module never holds state, which
is what makes the two engines provably agree.

The overlay NEVER opens positions and NEVER touches other accounts' books.
It closes (reduces to zero) the symbol whose stop fired, attributed to the
run like any other instruction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Sequence


class OverlayConfigError(ValueError):
    """Raised when a risk config is structurally invalid."""


@dataclass(frozen=True)
class RiskConfig:
    """Optional per-run risk overlay (all keys optional; absent = no overlay).

    - ``stop_loss_pct``: absolute loss from the position's entry (avg price),
      as a fraction (0.05 = -5%).
    - ``trailing_stop_pct``: drawdown from the position's peak marked value,
      as a fraction (0.10 = -10% from peak).
    - ``cooldown_bars``: after a stop fires, the symbol's target weight is
      treated as zero for this many rebalance points (weight track) and
      re-entry is suppressed for the same span (signal track).
    """

    stop_loss_pct: Decimal | None = None
    trailing_stop_pct: Decimal | None = None
    cooldown_bars: int = 0

    @property
    def active(self) -> bool:
        return (
            self.stop_loss_pct is not None
            or self.trailing_stop_pct is not None
            or self.cooldown_bars > 0
        )

    def to_dict(self) -> dict:
        out: dict = {}
        if self.stop_loss_pct is not None:
            out["stop_loss_pct"] = float(self.stop_loss_pct)
        if self.trailing_stop_pct is not None:
            out["trailing_stop_pct"] = float(self.trailing_stop_pct)
        if self.cooldown_bars:
            out["cooldown_bars"] = self.cooldown_bars
        return out

    @classmethod
    def from_dict(cls, data: dict | None) -> "RiskConfig":
        """Build from a run's optional ``risk`` dict; invalid values raise."""
        if not data:
            return cls()
        stop = data.get("stop_loss_pct")
        trail = data.get("trailing_stop_pct")
        cooldown = data.get("cooldown_bars", 0)
        if stop is not None:
            stop = Decimal(str(stop))
            if not (Decimal("0") < stop < Decimal("1")):
                raise OverlayConfigError(
                    f"risk.stop_loss_pct must be in (0, 1), got {stop}"
                )
        if trail is not None:
            trail = Decimal(str(trail))
            if not (Decimal("0") < trail < Decimal("1")):
                raise OverlayConfigError(
                    f"risk.trailing_stop_pct must be in (0, 1), got {trail}"
                )
        if cooldown is not None:
            cooldown = int(cooldown)
            if cooldown < 0:
                raise OverlayConfigError(
                    f"risk.cooldown_bars must be >= 0, got {cooldown}"
                )
        return cls(stop_loss_pct=stop, trailing_stop_pct=trail, cooldown_bars=cooldown)


@dataclass
class CooldownState:
    """Per-run cooldown bookkeeping (caller-owned; this module is stateless).

    ``remaining[symbol]`` counts down one per processed bar/rebalance point;
    a symbol with remaining > 0 must not be re-entered (weight treated as 0).
    """

    remaining: dict[str, int] = field(default_factory=dict)

    def suppressed(self, symbol: str) -> bool:
        return self.remaining.get(symbol, 0) > 0

    def arm(self, symbol: str, bars: int) -> None:
        if bars > 0:
            self.remaining[symbol] = bars

    def tick(self, symbols: Sequence[str] | None = None) -> None:
        """Count down one bar/rebalance point (optionally only given symbols)."""
        keys = list(self.remaining.keys()) if symbols is None else [
            s for s in symbols if s in self.remaining
        ]
        for s in keys:
            self.remaining[s] -= 1
            if self.remaining[s] <= 0:
                del self.remaining[s]


@dataclass(frozen=True)
class OverlayTrigger:
    """One stop trigger produced by an overlay evaluation."""

    symbol: str
    kind: str            # "stop_loss" | "trailing_stop"
    trigger_level: Decimal   # the price level the stop fired at
    fill_price: Decimal      # the price the close-out executes at (conservative)
    detail: str


def _close_price_for_bar(stop_level: Decimal, bar_open: Decimal, side: str) -> Decimal:
    """Gap-aware conservative fill for a bar-triggered stop (design D3).

    A sell stop closing a long fills at min(stop level, bar open) — if the bar
    gapped below the stop, the investor gets the open, not the wish. The bar's
    low proves the level traded; using the open when it is worse is the
    conservative direction and keeps backtest risk metrics from flattering.
    """
    if side == "SELL":
        return min(stop_level, bar_open)
    # A buy stop closing a short fills at max(stop level, bar open).
    return max(stop_level, bar_open)


def evaluate_bar_overlay(
    config: RiskConfig,
    symbol: str,
    side: str,
    entry_price: Decimal | None,
    peak_value: Decimal | None,
    bar_open: Decimal,
    bar_close: Decimal,
    bar_low: Decimal,
    bar_high: Decimal,
) -> OverlayTrigger | None:
    """Backtest-side per-bar overlay check.

    ``side`` is the position's side ("SELL" for a long position's protective
    stop, "BUY" for a short). ``peak_value`` is the caller's running marked
    peak (per unit or per position — the caller decides and stays consistent);
    ``None`` disables the trailing check for this bar.
    """
    if entry_price is None or entry_price <= 0:
        return None

    # Absolute stop-loss: long exits when low <= entry * (1 - stop_pct).
    if config.stop_loss_pct is not None:
        if side == "SELL":
            level = entry_price * (Decimal("1") - config.stop_loss_pct)
            if bar_low <= level:
                return OverlayTrigger(
                    symbol=symbol, kind="stop_loss", trigger_level=level,
                    fill_price=_close_price_for_bar(level, bar_open, side),
                    detail=f"bar low {bar_low} reached stop level {level}",
                )
        else:
            level = entry_price * (Decimal("1") + config.stop_loss_pct)
            if bar_high >= level:
                return OverlayTrigger(
                    symbol=symbol, kind="stop_loss", trigger_level=level,
                    fill_price=_close_price_for_bar(level, bar_open, side),
                    detail=f"bar high {bar_high} reached stop level {level}",
                )

    # Trailing stop: peak drawdown from the running peak.
    if config.trailing_stop_pct is not None and peak_value is not None:
        mark = bar_low if side == "SELL" else bar_high
        peak = max(peak_value, mark)
        drawdown_level = peak * (Decimal("1") - config.trailing_stop_pct)
        if side == "SELL" and bar_low <= drawdown_level:
            return OverlayTrigger(
                symbol=symbol, kind="trailing_stop",
                trigger_level=drawdown_level,
                fill_price=_close_price_for_bar(drawdown_level, bar_open, side),
                detail=f"trailing drawdown from peak {peak}",
            )
        if side == "BUY" and bar_high >= peak * (Decimal("1") + config.trailing_stop_pct):
            return OverlayTrigger(
                symbol=symbol, kind="trailing_stop",
                trigger_level=peak * (Decimal("1") + config.trailing_stop_pct),
                fill_price=_close_price_for_bar(
                    peak * (Decimal("1") + config.trailing_stop_pct), bar_open, side
                ),
                detail=f"trailing run-up from trough {peak}",
            )
    return None


def evaluate_quote_overlay(
    config: RiskConfig,
    symbol: str,
    side: str,
    entry_price: Decimal | None,
    peak_value: Decimal | None,
    quote_bid: Decimal,
    quote_ask: Decimal,
) -> OverlayTrigger | None:
    """Live-side per-tick overlay check against the current quote.

    The exit price is the achievable side of the quote (bid for a long's
    sell, ask for a short's buy) — the live analogue of the backtest's
    conservative open-fill rule.
    """
    if entry_price is None or entry_price <= 0:
        return None
    exit_price = quote_bid if side == "SELL" else quote_ask

    if config.stop_loss_pct is not None:
        if side == "SELL":
            level = entry_price * (Decimal("1") - config.stop_loss_pct)
            if quote_bid <= level:
                return OverlayTrigger(
                    symbol=symbol, kind="stop_loss", trigger_level=level,
                    fill_price=exit_price,
                    detail=f"bid {quote_bid} at/below stop level {level}",
                )
        else:
            level = entry_price * (Decimal("1") + config.stop_loss_pct)
            if quote_ask >= level:
                return OverlayTrigger(
                    symbol=symbol, kind="stop_loss", trigger_level=level,
                    fill_price=exit_price,
                    detail=f"ask {quote_ask} at/above stop level {level}",
                )

    if config.trailing_stop_pct is not None and peak_value is not None:
        mark = quote_bid if side == "SELL" else quote_ask
        peak = max(peak_value, mark)
        if side == "SELL":
            level = peak * (Decimal("1") - config.trailing_stop_pct)
            if quote_bid <= level:
                return OverlayTrigger(
                    symbol=symbol, kind="trailing_stop", trigger_level=level,
                    fill_price=exit_price,
                    detail=f"trailing drawdown from peak {peak}",
                )
        else:
            level = peak * (Decimal("1") + config.trailing_stop_pct)
            if quote_ask >= level:
                return OverlayTrigger(
                    symbol=symbol, kind="trailing_stop", trigger_level=level,
                    fill_price=exit_price,
                    detail=f"trailing run-up from trough {peak}",
                )
    return None


def effective_target_weight(
    symbol: str, target_weight: Decimal, cooldown: CooldownState
) -> Decimal:
    """Weight-track cooldown: a cooling symbol's effective target weight is 0
    for the cooldown span; the strategy's own target resumes afterwards."""
    if cooldown.suppressed(symbol):
        return Decimal("0")
    return target_weight
