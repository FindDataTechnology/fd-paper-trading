"""Multi-symbol weight-mode replay (design D2/D3/D4, spec: strategy-weight-mode).

The single-symbol backtester replays one series and applies orders signals. This
module replays a *universe*: at each rebalance point the strategy states target
weights, the engine converts the difference from the current portfolio into
integer-share instructions, and the portfolio is revalued at the latest available
closes. Both live in this engine so that the backtest and the paper run cannot
disagree about what a target weight means.

Three rules here are the whole reason this is a separate module — each one exists
because the naive version is silently wrong:

- **Rounding down to whole shares, remainder to cash** (D3). Rounding up would
  let a portfolio hold more than its cash; the dropped fraction is reported per
  instruction rather than absorbed, so "target 0.5, actual 0.47" stays explicable.
- **A halted symbol is valued but not traded** (D4). Its position is marked at the
  most recent close and any instruction for it is skipped with a note; the
  alternative — forward-filling and trading anyway — shows fills that could not
  have happened.
- **Weights come from the contract layer** (D5). A target mapping is validated
  before it can move money; an illegal one fails that period's evaluation with the
  contract's own message and produces no instructions, rather than being scaled
  into legality.

Scale-downs are recorded, not hidden: a buy constrained by cash and a sell
constrained by the position both carry the reason, so the difference between what
was asked for and what happened is always attributable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Callable, Mapping, Sequence

from ..marketdata.provider import Bar
from .backtester import EvaluationError
from .ctx import SymbolSeries, WeightContext
from .spec import WeightSpec
from .weights import validate_weights

#: Cash is not a symbol; a weight mapping may name it only as the residual.
CASH_LABEL = "CASH"


@dataclass
class Instruction:
    """One intended trade, after rounding and any constraint scaling."""

    symbol: str
    side: str  # "BUY" | "SELL"
    quantity: Decimal
    price: Decimal
    #: Quantity before constraint scaling (equal to quantity when unscaled).
    requested_quantity: Decimal
    scaled: bool = False
    scale_reason: str | None = None
    #: Set when the symbol could not be traded on the rebalance date (halted).
    skipped: bool = False
    skip_reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "quantity": str(self.quantity),
            "price": str(self.price),
            "scaled": self.scaled,
            "scale_reason": self.scale_reason,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
        }


@dataclass
class RebalanceSnapshot:
    """What the engine did at one rebalance point: target, actual, instructions."""

    day: date
    target_weights: dict[str, float]
    actual_weights: dict[str, float]
    instructions: list[Instruction] = field(default_factory=list)
    cash_floor: float = 0.0
    #: Symbols valued from a stale close on this date (they had no bar).
    stale_symbols: list[str] = field(default_factory=list)
    #: Set when this period's target was refused (illegal weights); no orders.
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "date": self.day.isoformat(),
            "target_weights": dict(self.target_weights),
            "actual_weights": dict(self.actual_weights),
            "cash_floor": self.cash_floor,
            "stale_symbols": list(self.stale_symbols),
            "error": self.error,
            "instructions": [i.to_dict() for i in self.instructions],
        }


@dataclass
class WeightBacktestResult:
    stats: dict
    equity_points: list[tuple[date, Decimal]]
    snapshots: list[RebalanceSnapshot]
    universe: list[str]
    #: Every (date, symbol) pair valued from a stale close, for the run record.
    data_gaps: list[dict]


class WeightSource:
    """The strategy side of a weight run: a target mapping per rebalance date."""

    def __init__(
        self,
        spec: WeightSpec,
        series: Mapping[str, SymbolSeries],
    ) -> None:
        self._spec = spec
        self._series = dict(series)
        self._dates: list[date] | None = None

    def target(
        self, day: date, ctx: WeightContext, index: int
    ) -> tuple[dict[str, float], float, str | None]:
        """Target weights for ``day``: ``(weights, cash_floor, error)``.

        A static series uses the most recent period at or before ``day`` (before
        the first period the portfolio is empty by design, not an error). A code
        source is evaluated through ``ctx`` by the caller-provided evaluator; both
        paths produce a mapping that is validated the same way by ``validate_spec``
        or ``validate_weights`` — one rule, two sources.
        """
        if self._spec.weights_series is not None:
            return self._static_target(day)
        raise EvaluationError("code track requires an evaluator")

    def _static_target(self, day: date) -> tuple[dict[str, float], float, str | None]:
        series = self._spec.weights_series or {}
        eligible = sorted(k for k in series if date.fromisoformat(k) <= day)
        if not eligible:
            return {}, 0.0, None  # before the first period: empty by design
        key = eligible[-1]
        return dict(series[key]), float(self._spec.cash_floors.get(key, 0.0)), None


def build_weight_evaluator(
    spec: WeightSpec,
) -> Callable[[WeightContext], Mapping[str, float]] | None:
    """The ``evaluate_code`` callable for a weight spec, or None for a static one.

    A ``weights_series`` spec needs no evaluator: the engine reads each period's
    mapping directly (the ``WeightSource`` path). A ``source`` spec runs in the
    existing sandbox subprocess, so the code track's AST allowlist and resource
    limits apply unchanged — the only difference from ``on_bar`` is the entry
    point name and the context the source receives.
    """
    if spec.weights_series is not None:
        return None
    if not spec.source:
        raise EvaluationError(
            "weight spec has neither weights_series nor source"
        )
    from .sandbox_worker import _WeightSandboxEvaluator

    return _WeightSandboxEvaluator(spec.source).evaluate


def _bar_date(bar: Bar) -> date:
    return datetime.fromtimestamp(bar.ts, tz=timezone.utc).date()


class _WeightPortfolio:
    """Cash plus per-symbol integer positions, valued at latest closes."""

    def __init__(self, cash: Decimal) -> None:
        self.cash = cash
        self.positions: dict[str, Decimal] = {}

    def position(self, symbol: str) -> Decimal:
        return self.positions.get(symbol, Decimal(0))

    def equity(self, marks: Mapping[str, Decimal]) -> Decimal:
        total = self.cash
        for sym, qty in self.positions.items():
            if qty:
                total += qty * marks.get(sym, Decimal(0))
        return total

    def weights(self, marks: Mapping[str, Decimal]) -> dict[str, float]:
        equity = self.equity(marks)
        if equity <= 0:
            return {}
        return {
            sym: float(qty * marks.get(sym, Decimal(0)) / equity)
            for sym, qty in self.positions.items()
            if qty
        }


def _marks_for(
    day: date, universe: Sequence[str], series: Mapping[str, SymbolSeries]
) -> tuple[dict[str, Decimal], list[str]]:
    """Latest close per symbol, plus the symbols whose close is stale on ``day``.

    A symbol with no bar at all contributes no mark and no gap note: it simply has
    no value to report, which is different from a symbol that traded before and
    not today.
    """
    marks: dict[str, Decimal] = {}
    stale: list[str] = []
    for sym in universe:
        found = series.get(sym)
        if found is None:
            continue
        close = found.latest_close()
        if close is None:
            continue
        marks[sym] = Decimal(str(close))
        if not found.has_bar_on(day):
            stale.append(sym)
    return marks, stale


def build_series(
    universe: Sequence[str],
    bars_by_symbol: Mapping[str, list[Bar]],
    day: date,
) -> dict[str, SymbolSeries]:
    """Per-symbol series truncated to bars at or before ``day`` (no look-ahead)."""
    out: dict[str, SymbolSeries] = {}
    for sym in universe:
        bars = [
            b for b in (bars_by_symbol.get(sym) or []) if _bar_date(b) <= day
        ]
        out[sym] = SymbolSeries(sym, bars)
    return out


def replay_weights(
    spec: WeightSpec,
    bars_by_symbol: Mapping[str, list[Bar]],
    initial_cash: Decimal,
    *,
    evaluate_code: Callable[[WeightContext], Mapping[str, float]] | None = None,
) -> WeightBacktestResult:
    """Replay a universe, rebalancing every ``every_n_bars`` trading dates.

    The date axis is the union of the universe's bar dates (a date on which any
    universe member traded is a decision date); the cadence is counted over that
    axis rather than over calendar days, so "every 5 bars" means five decisions,
    not five days off a calendar that includes weekends.
    """
    universe = list(spec.universe)
    axis = sorted(
        {
            _bar_date(b)
            for sym in universe
            for b in (bars_by_symbol.get(sym) or [])
        }
    )
    if not axis:
        raise EvaluationError(
            "no bars for any universe symbol in the requested window"
        )

    portfolio = _WeightPortfolio(initial_cash)
    curve: list[tuple[date, Decimal]] = []
    equity_values: list[Decimal] = []
    snapshots: list[RebalanceSnapshot] = []
    gaps: list[dict] = []
    source = WeightSource(spec, {})
    every = spec.rebalance.every_n_bars
    prior_marks: dict[str, Decimal] = {}

    for index, day in enumerate(axis):
        series = build_series(universe, bars_by_symbol, day)
        marks, stale = _marks_for(day, universe, series)
        for sym in stale:
            gaps.append({"date": day.isoformat(), "symbol": sym})

        is_rebalance = index % every == 0
        if is_rebalance:
            snapshot = RebalanceSnapshot(
                day=day,
                target_weights={},
                actual_weights={},
                stale_symbols=list(stale),
            )
            ctx = WeightContext(
                day=day,
                universe=universe,
                series=series,
                cash=portfolio.cash,
                positions=portfolio.positions,
            )
            try:
                if evaluate_code is not None:
                    target = dict(evaluate_code(ctx))
                    cash_floor = 0.0
                else:
                    target, cash_floor, _ = source.target(day, ctx, index)
                target = {str(k).upper(): float(v) for k, v in target.items()}
                target_symbols = {k: v for k, v in target.items() if k != CASH_LABEL}
                errors = validate_weights(universe, target_symbols, cash_floor=cash_floor)
                if errors:
                    snapshot.error = "; ".join(errors)
                    snapshot.actual_weights = portfolio.weights(marks)
                    snapshots.append(snapshot)
                    equity = portfolio.equity(marks)
                    equity_values.append(equity)
                    curve.append((day, equity))
                    continue
                snapshot.target_weights = dict(target)
                snapshot.cash_floor = float(cash_floor)
                snapshot.instructions = _rebalance(
                    portfolio, marks, stale, target_symbols, day
                )
            except EvaluationError as exc:
                snapshot.error = str(exc)
            except Exception as exc:
                snapshot.error = f"evaluation failed: {exc}"
            # Recorded after the instructions are applied: the snapshot is the
            # evidence of what the engine actually holds, which is exactly where
            # rounding and scale-downs make it differ from the target (D7).
            snapshot.actual_weights = portfolio.weights(marks)
            snapshots.append(snapshot)

        equity = portfolio.equity(marks)
        equity_values.append(equity)
        curve.append((day, equity))
        prior_marks = marks

    stats = _stats(equity_values, initial_cash, snapshots)
    return WeightBacktestResult(
        stats=stats,
        equity_points=curve,
        snapshots=snapshots,
        universe=universe,
        data_gaps=gaps,
    )


def _rebalance(
    portfolio: _WeightPortfolio,
    marks: Mapping[str, Decimal],
    stale: Sequence[str],
    target: Mapping[str, float],
    day: date,
) -> list[Instruction]:
    """Turn target weights into instructions, applying the three rules in order.

    Order matters: value the portfolio, compute each symbol's target value, round
    *down* to whole shares, then let cash bind the buys (a sell cannot change the
    cash total before the buys are sized, so buys are constrained against the cash
    that the intended sells would free — the intra-rebalance overcommit a naive
    implementation shows as a rejected order).

    No separate ``cash_floor`` rule appears here, and that is not an omission: the
    contract already refuses any instance whose ``Σweights + cash_floor`` exceeds
    1, so spending to the target weights leaves cash at ``1 - Σweights``, which is
    at or above the floor by construction. Adding a second cash check here would be
    a second rule that can drift; rounding only ever spends *less* than the target.
    """
    equity = portfolio.equity(marks)
    if equity <= 0:
        return []
    instructions: list[Instruction] = []

    # A target mapping states the *whole* intended portfolio, so a held symbol
    # absent from it is a target of zero — liquidate. Treating "absent" as
    # "leave it alone" would make a rotation impossible to express: the strategy
    # would have to name a symbol it wants nothing to do with.
    weights = dict(target)
    for sym, qty in portfolio.positions.items():
        if qty and sym not in weights:
            weights[sym] = 0.0

    # Sells first: they free cash, and their quantity is bound by the position.
    buys: list[tuple[str, Decimal, Decimal]] = []
    for sym, weight in sorted(weights.items()):
        mark = marks.get(sym)
        if mark is None or mark <= 0:
            instructions.append(
                Instruction(
                    symbol=sym,
                    side="BUY",
                    quantity=Decimal(0),
                    price=Decimal(0),
                    requested_quantity=Decimal(0),
                    skipped=True,
                    skip_reason="no price available (symbol never traded in window)",
                )
            )
            continue
        target_value = equity * Decimal(str(weight))
        current_value = portfolio.position(sym) * mark
        delta_value = target_value - current_value
        raw_qty = abs(delta_value) / mark
        qty = Decimal(int(math.floor(raw_qty)))  # down to whole shares (D3)
        if qty == 0:
            continue
        if sym in stale:
            instructions.append(
                Instruction(
                    symbol=sym,
                    side="BUY" if delta_value > 0 else "SELL",
                    quantity=Decimal(0),
                    price=mark,
                    requested_quantity=qty,
                    skipped=True,
                    skip_reason=(
                        f"no bar on {day.isoformat()}; valued at the last close "
                        f"but not tradable (halted)"
                    ),
                )
            )
            continue
        if delta_value < 0:
            instructions.append(
                Instruction(
                    symbol=sym,
                    side="SELL",
                    quantity=qty,
                    price=mark,
                    requested_quantity=qty,
                )
            )
        else:
            buys.append((sym, qty, mark))

    # Cash freed by the intended sells (net of nothing — buys are not yet placed).
    spendable = portfolio.cash + sum(
        (i.quantity * i.price for i in instructions if i.side == "SELL"), Decimal(0)
    )
    for sym, qty, mark in buys:
        cost = qty * mark
        if cost > spendable:
            affordable = Decimal(int(math.floor(spendable / mark)))
            if affordable <= 0:
                instructions.append(
                    Instruction(
                        symbol=sym,
                        side="BUY",
                        quantity=Decimal(0),
                        price=mark,
                        requested_quantity=qty,
                        scaled=True,
                        scale_reason=(
                            f"insufficient cash: needed {cost} for {qty} shares, "
                            f"{spendable} available"
                        ),
                    )
                )
                continue
            instructions.append(
                Instruction(
                    symbol=sym,
                    side="BUY",
                    quantity=affordable,
                    price=mark,
                    requested_quantity=qty,
                    scaled=True,
                    scale_reason=(
                        f"insufficient cash: reduced from {qty} to {affordable} shares"
                    ),
                )
            )
            spendable -= affordable * mark
        else:
            instructions.append(
                Instruction(
                    symbol=sym,
                    side="BUY",
                    quantity=qty,
                    price=mark,
                    requested_quantity=qty,
                )
            )
            spendable -= cost

    # The position bound is *structural*, not a runtime branch: qty = floor(|Δ|/mark)
    # with Δ = target_value - pos·mark and a non-negative target_value means
    # qty = floor(pos - target_value/mark) ≤ pos, always. So no scale-down can occur
    # here in the backtest, and none is applied — inventing a branch that cannot fire
    # would advertise a guarantee nobody tests. What the live path needs is not a
    # clamp but the same construction: derive every sell from the ledger's own
    # position, which is what makes the spec's "sells are bounded by holdings" true
    # by arithmetic rather than by a check. ``assert`` documents that reasoning at
    # the one place a future change to the sizing basis would break it.
    for ins in instructions:
        if ins.side == "SELL" and not ins.skipped:
            assert ins.quantity <= portfolio.position(ins.symbol), (
                f"sell {ins.quantity} of {ins.symbol} exceeds held "
                f"{portfolio.position(ins.symbol)} — the sizing basis changed"
            )

    for ins in instructions:
        if ins.skipped or ins.quantity <= 0:
            continue
        signed = ins.quantity if ins.side == "BUY" else -ins.quantity
        portfolio.cash -= signed * ins.price
        portfolio.positions[ins.symbol] = portfolio.position(ins.symbol) + signed
        if portfolio.positions[ins.symbol] == 0:
            del portfolio.positions[ins.symbol]
    return instructions


def _stats(
    equity: Sequence[Decimal],
    initial_cash: Decimal,
    snapshots: Sequence[RebalanceSnapshot],
) -> dict:
    """Portfolio-level stats. Same six fields as the single-symbol path (spec:

    the weight track's stats must be comparable), computed over the portfolio
    equity curve rather than a single position.
    """
    from .backtester import _max_drawdown, _sharpe

    final = equity[-1]
    total_return = float(final / initial_cash) - 1.0 if initial_cash else 0.0
    sharpe = _sharpe(list(equity))
    trades = [i for s in snapshots for i in s.instructions if not i.skipped and i.quantity > 0]
    scaled = [i for i in trades if i.scaled]
    return {
        "total_return": round(total_return, 6),
        "max_drawdown": round(float(_max_drawdown(list(equity))), 6),
        "sharpe": round(sharpe, 6) if sharpe is not None else None,
        # Not a per-trip win rate like the single-symbol path: an open portfolio has
        # no closed trips to count, so reporting None is honest where a number
        # would be an artifact of the accounting.
        "win_rate": None,
        "trade_count": len(trades),
        "final_equity": round(float(final), 4),
        "rebalance_count": len([s for s in snapshots if s.error is None]),
        "scaled_instruction_count": len(scaled),
        "periods_without_orders": [s.day.isoformat() for s in snapshots if s.error is not None],
    }
