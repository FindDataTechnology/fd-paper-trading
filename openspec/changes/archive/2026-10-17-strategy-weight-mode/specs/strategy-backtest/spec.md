## MODIFIED Requirements

### Requirement: Backtest run over real daily bars

The system SHALL execute a backtest run by replaying historical daily bars (oldest-first) for the strategy's symbol through the same `on_bar(ctx)` evaluation contract used by the live runner. Backtests SHALL run as in-process background tasks with statuses `QUEUED -> RUNNING -> COMPLETED | FAILED`. The backtest request SHALL accept `start`, `end`, and `initial_cash`; `end - start` exceeding the user's group `max_backtest_days` SHALL be rejected. The request SHALL additionally accept optional `cost_model` (commission/tax/slippage configuration, see the cost model requirement), optional `benchmarks` (list of benchmark symbols), and optional walk-forward window parameters (`train_window`/`test_window`/`step`, see the walk-forward capability) — all optional, with backward-compatible defaults. For weight-track strategies, the backtest SHALL replay the universe's date-aligned daily bars through the `target_weights(ctx)` contract at each rebalance point, maintaining a portfolio of cash plus per-symbol positions (see the strategy-weight-mode capability for the weight-mode replay, rounding, and constraint semantics).

#### Scenario: Backtest completes over a bar window
- **WHEN** a user requests a backtest of a valid strategy over a window with available bars
- **THEN** the run transitions to `COMPLETED` and records stats and an equity curve

#### Scenario: Window beyond group limit rejected
- **WHEN** a user requests a backtest whose `end - start` exceeds their group's `max_backtest_days`
- **THEN** the system responds `400` identifying the limit

#### Scenario: Backtest requires no account
- **WHEN** a backtest run is created
- **THEN** it has `mode = BACKTEST` and a null `account_id` (the portfolio is simulated in memory)

#### Scenario: Request without new optional fields is unchanged
- **WHEN** a backtest is requested with only `start`, `end`, and `initial_cash`
- **THEN** the run executes with zero-cost, no-benchmark, single-window behavior identical to before

#### Scenario: Weight-track backtest replays the universe
- **WHEN** a backtest is requested for a weight-track strategy with a non-empty universe
- **THEN** the run replays date-aligned bars for the universe, applies `target_weights` at rebalance points, and records the portfolio equity curve

## ADDED Requirements

### Requirement: Weight-mode run records

For a weight-track backtest or paper run, the system SHALL record per-rebalance weight snapshots and per-rebalance instruction details (symbol, side, quantity, and whether the instruction was scaled down by a constraint) on the run, in addition to the existing stats and equity points. Runs SHALL record their `universe` at start; editing the strategy afterward SHALL NOT alter a recorded run's universe or snapshots.

#### Scenario: Snapshots and instructions recorded
- **WHEN** a weight-track backtest completes
- **THEN** its detail response contains the per-rebalance weight snapshots and instruction details with scaling flags

#### Scenario: Universe pinned at run start
- **WHEN** a weight strategy is edited to a different universe after a run was recorded
- **THEN** the run still shows its original universe and snapshots
