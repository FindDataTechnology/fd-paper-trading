# strategy-live-execution Specification (delta)

## ADDED Requirements

### Requirement: Paper run lifecycle

The system SHALL support live paper runs binding a strategy to one of the user's paper accounts. A paper run SHALL have statuses `STARTING → RUNNING → STOPPED | ERROR`. Starting a run SHALL pin the strategy's current `strategy_version` and require an `account_id`. Editing the strategy SHALL NOT change a live run's behavior; the API/UI SHALL surface "newer version available, restart to apply".

#### Scenario: Start pins version and account
- **WHEN** a user starts a paper run for a strategy at version 2 on account A
- **THEN** the run records `mode = PAPER`, `strategy_version = 2`, and `account_id = A`

#### Scenario: Edit does not affect a live run
- **WHEN** a user edits the strategy (bumping version to 3) while a version-2 run is `RUNNING`
- **THEN** the run continues evaluating the version-2 spec and reports that a newer version exists

### Requirement: Concurrent-run limit per group

Starting a paper run SHALL be rejected with `409` when the user already has `max_active_strategies` (their group's config) runs in `STARTING`/`RUNNING` status.

#### Scenario: Limit reached
- **WHEN** a user whose group allows 3 active strategies starts a 4th paper run
- **THEN** the system responds `409` naming the limit

### Requirement: Daily-bar tick loop

A `RUNNING` paper run SHALL wake on a poll interval (default 30 minutes), check whether a new daily bar exists for the strategy's symbol (bar date newer than the last processed date), and — only when a new bar exists — evaluate the strategy's `on_bar(ctx)` against bar history plus the account's current position/cash. A poll with no new bar (weekend, holiday, data gap) SHALL be a no-op. A tick whose evaluation fails (sandbox timeout/crash) SHALL be logged to the run and skipped without stopping the run.

#### Scenario: New bar triggers evaluation
- **WHEN** a poll finds a daily bar dated after the run's last processed bar
- **THEN** the strategy is evaluated once for that bar and the last-processed date advances

#### Scenario: No new bar is a no-op
- **WHEN** a poll finds no newer daily bar
- **THEN** no evaluation occurs and no orders are placed

#### Scenario: Evaluation failure skips the tick
- **WHEN** a code-track strategy's evaluation times out on a tick
- **THEN** the error is recorded on the run, the run stays `RUNNING`, and no orders are placed for that bar

### Requirement: Signals become real paper orders

Each signal emitted by evaluation SHALL be placed as an order through the existing execution service on the run's bound account: MARKET signals as market orders, LIMIT signals (with `limit_price`) as limit orders. These orders SHALL carry the run's `strategy_run_id` attribution and SHALL be subject to the same buying-power checks, rate limiting, and ledgering as manual orders. A signal rejected by the execution layer (e.g. insufficient buying power) SHALL be logged to the run and SHALL NOT stop it.

#### Scenario: Market signal fills like a manual order
- **WHEN** a running strategy emits `ctx.buy(10)` and the account has sufficient cash
- **THEN** a market BUY order is created, filled, and visible in the orders API with the run's attribution

#### Scenario: Limit signal rests like a manual order
- **WHEN** a running strategy emits `ctx.sell(5, limit_price=p)` with `p` above the current quote
- **THEN** a resting limit SELL order is created with the run's attribution

#### Scenario: Rejected signal logged, run continues
- **WHEN** a signal is rejected for insufficient buying power
- **THEN** the rejection is recorded on the run's events and the run stays `RUNNING`

### Requirement: Per-run daily order cap

Each paper run SHALL place at most `max_orders_per_day` (the user's group config) orders per calendar day. Signals beyond the cap SHALL be skipped and logged to the run.

#### Scenario: Cap enforced
- **WHEN** a run has already placed its group's daily order cap and emits another signal the same day
- **THEN** the signal is skipped and a cap event is logged on the run

### Requirement: Stop semantics

Stopping a paper run SHALL transition it to `STOPPED`, cancel that run's resting (non-terminal) LIMIT orders via the existing cancellation path, and SHALL NOT close positions or cancel other orders. Positions opened by the run remain the account's property.

#### Scenario: Stop cancels resting limits only
- **WHEN** a run with two resting limit orders and one filled position is stopped
- **THEN** the two resting orders become `CANCELLED` and the position is unchanged

### Requirement: Runner supervision and error state

Paper runs SHALL execute as supervised in-process asyncio tasks. A runner task that dies from an unexpected error SHALL transition its run to `ERROR` with the error recorded — a run SHALL never silently stop ticking.

#### Scenario: Runner crash surfaces as ERROR
- **WHEN** a runner task raises an unexpected exception outside bar evaluation
- **THEN** the run transitions to `ERROR` with a recorded message

### Requirement: Paper-run equity snapshots and events

Each paper run SHALL persist a daily equity point (cash + positions marked at the latest close) after the first tick that processes a new bar. Strategy events (run state changes, orders placed, rejections, errors) SHALL be recorded on the run and broadcast over the existing `/ws` connection manager to the owning user so the web UI updates live.

#### Scenario: Daily snapshot after evaluation
- **WHEN** a run processes a new daily bar
- **THEN** an equity point for that date is persisted

#### Scenario: Event broadcast on order
- **WHEN** a run places an order
- **THEN** a strategy event is broadcast to the owning user's WebSocket connections

### Requirement: Run history API and web views

The system SHALL expose `GET /api/v1/strategy-runs` and `GET /api/v1/strategy-runs/{id}` covering both modes, including status, pinned version, stats/snapshots, attributed orders, and events. The web strategy detail page SHALL show the active run's live status and a run-history table (mode, window/dates, status, headline stats) linking to per-run detail.

#### Scenario: Run history lists both modes
- **WHEN** a user lists runs for a strategy with one backtest and one paper run
- **THEN** both appear with their mode, status, and pinned version

#### Scenario: Orders attributed to a run are queryable
- **WHEN** a client requests a paper run's detail
- **THEN** the response includes (or links) the orders placed by that run
