## MODIFIED Requirements

### Requirement: Daily-bar tick loop

A `RUNNING` paper run SHALL wake on a poll interval (default 30 minutes), check whether a new daily bar exists for the strategy's symbol (bar date newer than the last processed date), and - only when a new bar exists - evaluate the strategy's `on_bar(ctx)` against bar history plus the account's current position/cash. A poll with no new bar (weekend, holiday, data gap) SHALL be a no-op. A tick whose evaluation fails (sandbox timeout/crash) SHALL be logged to the run and skipped without stopping the run. For weight-track runs, the tick SHALL consider the universe: a tick is actionable when a new bar exists for the run's primary (first) universe symbol, and evaluation SHALL call `target_weights(ctx)`; the resulting instructions SHALL be executed only at rebalance points (see the weight-mode paper-trading requirement), while non-rebalance ticks that process a new bar SHALL still record the daily equity snapshot.

#### Scenario: New bar triggers evaluation
- **WHEN** a poll finds a daily bar dated after the run's last processed bar
- **THEN** the strategy is evaluated once for that bar and the last-processed date advances

#### Scenario: No new bar is a no-op
- **WHEN** a poll finds no newer daily bar
- **THEN** no evaluation occurs and no orders are placed

#### Scenario: Evaluation failure skips the tick
- **WHEN** a code-track strategy's evaluation times out on a tick
- **THEN** the error is recorded on the run, the run stays `RUNNING`, and no orders are placed for that bar

#### Scenario: Weight run snapshots on non-rebalance ticks
- **WHEN** a weight-track run processes a new bar that is not a rebalance point
- **THEN** no orders are placed and the daily equity snapshot is still recorded

## ADDED Requirements

### Requirement: Weight-mode paper trading

A weight-track paper run SHALL convert the target-weights difference (target minus current portfolio weights, valued at the latest closes) into per-symbol MARKET orders through the existing `PaperExecutionService` at each rebalance point; buys are constrained by cash/buying power and sells by the account's positions, all orders carry the run's `strategy_run_id` attribution and are subject to the existing rate limits and ledger. Quantities SHALL be integer shares (rounded down, remainder left in cash). All orders produced by one rebalance SHALL count toward the run's daily order cap (`max_orders_per_day`); instructions beyond the cap SHALL be skipped and logged to the run. Starting a weight-track run SHALL reject a universe exceeding the user group's `max_universe_size` with the limit named.

#### Scenario: Rebalance converts to multi-symbol orders
- **WHEN** a running weight strategy emits `{A:0.5, B:0.5}` at a rebalance point while the account holds no positions
- **THEN** two attributed MARKET buy orders are created and, after execution, positions match the target within integer-rounding remainder kept in cash

#### Scenario: Cap applies to rebalance instructions
- **WHEN** a rebalance needs more orders than the day's remaining cap
- **THEN** the excess instructions are skipped and logged, and the emitted ones execute normally

#### Scenario: Universe size limit enforced at start
- **WHEN** a weight-track run is started with a universe larger than the group's `max_universe_size`
- **THEN** the request is rejected naming the limit and the actual size

#### Scenario: Stop semantics unchanged for weight runs
- **WHEN** a weight-track run is stopped
- **THEN** resting limit orders of that run are cancelled, positions are untouched, and no flattening occurs
