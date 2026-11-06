## ADDED Requirements

### Requirement: Run-level risk overlay shared by backtest and live

A strategy run (signal-track or weight-track, backtest or live paper) MAY carry an optional
`risk` configuration: `stop_loss_pct` (absolute loss from entry), `trailing_stop_pct`
(drawdown from the position's peak value), and `cooldown_bars`. The same overlay logic
SHALL evaluate in both engines — the backtester per bar and the live runner per tick — so a
backtest cannot disagree with its own live run about when a stop fires.

Backtest trigger semantics on daily bars: a stop sell fires when the bar's `low` reaches the
stop level; the fill price SHALL be the worse of the stop level and that bar's `open`
(gap-aware, conservative). Live trigger semantics: the overlay evaluates against the current
quote each tick. On trigger, the runner/backtester SHALL emit a close-out (reduce to zero)
market instruction attributed to the run, counted against the run's existing daily order
cap (live) and recorded as strategy events (both engines). A triggered symbol's target
weight SHALL be treated as zero for `cooldown_bars` rebalance points (weight track) and
re-entry suppressed for the same span (signal track); after the cooldown the strategy's own
logic resumes control. The overlay SHALL NOT close other accounts' positions and SHALL NOT
open positions.

Runs SHALL record in their result which overlay (if any) was configured and which bars/ticks
triggered — a user comparing two runs SHALL be able to see that one had stops and the other
did not.

#### Scenario: Backtest stop fires on a gap-down bar

- **WHEN** a backtest run with `stop_loss_pct=5` holds a position entered at `100` and the
  next bar opens at `90` with low `89`
- **THEN** the stop fires on that bar and the fill is recorded at `90` (the open, worse than
  the stop level), not at `95`

#### Scenario: Live stop closes the position via an attributed order

- **WHEN** a live paper run with `stop_loss_pct=5` holds a position whose quote-derived
  value falls 5% below entry
- **THEN** the runner places an attributed market close-out order counted against the
  run's daily order cap, and a strategy event is recorded and broadcast

#### Scenario: Weight-mode cooldown prevents instant re-buy

- **WHEN** a weight-track run's stop fires on symbol `A` with `cooldown_bars=2`, and the
  next two rebalance points target a nonzero weight for `A`
- **THEN** `A`'s effective target weight is zero at both points and the original target
  resumes at the third

#### Scenario: No risk config means unchanged behavior

- **WHEN** a run is started without a `risk` configuration
- **THEN** neither engine applies any overlay and results are bit-identical to the
  pre-overlay behavior

#### Scenario: Overlay provenance is recorded

- **WHEN** two runs of the same strategy are compared, one with and one without stops
- **THEN** each run's record shows its `risk` configuration and triggered bars/ticks

### Requirement: Live runs record the adjustment caliber of their price world

A live paper run SHALL record the adjustment caliber declared by its market-data provider
(when the provider exposes one) at run start, and SHALL emit a run event when the provider's
data fingerprint for a followed symbol changes between ticks for reasons other than a new
bar (a re-adjustment, e.g. after an ex-dividend event). Runs SHALL NOT mix calibers within
one run: the bar window, quotes, and fills of a run come from one provider instance under
the existing one-price-world requirement. Re-adjustment SHALL NOT be silently swallowed
(the event makes indicator discontinuity explainable), and SHALL NOT alter the ledger:
entry prices already recorded stay as they were.

#### Scenario: Caliber recorded at run start

- **WHEN** a live run starts on a provider that declares `adjusted="forward"`
- **THEN** the run record carries that caliber

#### Scenario: Re-adjustment between ticks is surfaced

- **WHEN** an ex-dividend event re-adjusts the historical series and the run fetches its
  next window
- **THEN** a run event names the symbol and the fingerprint change, and the run continues on
  the new (internally consistent) window

#### Scenario: Ledger is not rewritten by re-adjustment

- **WHEN** a re-adjustment event occurs while a position is held
- **THEN** the position's recorded entry price and quantity are unchanged
