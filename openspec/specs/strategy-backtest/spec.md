# strategy-backtest Specification

## Purpose

约束策略回测：真实日线序列上的回测运行、权益曲线与统计（收益/回撤/夏普/胜率）、异步运行生命周期。
## Requirements
### Requirement: Backtest run over real daily bars

The system SHALL execute a backtest run by replaying historical daily bars (oldest-first) for the strategy's symbol through the same `on_bar(ctx)` evaluation contract used by the live runner. Backtests SHALL run as in-process background tasks with statuses `QUEUED -> RUNNING -> COMPLETED | FAILED`. The backtest request SHALL accept `start`, `end`, and `initial_cash`; `end - start` exceeding the user's group `max_backtest_days` SHALL be rejected.

#### Scenario: Backtest completes over a bar window
- **WHEN** a user requests a backtest of a valid strategy over a window with available bars
- **THEN** the run transitions to `COMPLETED` and records stats and an equity curve

#### Scenario: Window beyond group limit rejected
- **WHEN** a user requests a backtest whose `end - start` exceeds their group's `max_backtest_days`
- **THEN** the system responds `400` identifying the limit

#### Scenario: Backtest requires no account
- **WHEN** a backtest run is created
- **THEN** it has `mode = BACKTEST` and a null `account_id` (the portfolio is simulated in memory)

### Requirement: Fill-at-close simulation

The backtester SHALL maintain an in-memory portfolio (cash + signed position) initialized to `initial_cash`. MARKET signals SHALL fill at the signal bar's close. LIMIT buy signals SHALL fill at the limit price if the bar's low is at or below the limit price; LIMIT sell signals SHALL fill at the limit price if the bar's high is at or above the limit price; otherwise the limit signal expires unfilled at that bar's end (documented optimistic assumption, no queue modeling). A buy whose cost exceeds available cash SHALL be skipped (not executed).

#### Scenario: Market buy fills at signal-bar close
- **WHEN** a strategy emits `ctx.buy(10)` on a bar with close `c` and sufficient cash
- **THEN** the simulated position increases by 10 and cash decreases by `10 * c`

#### Scenario: Limit buy fills when bar low crosses
- **WHEN** a strategy emits `ctx.buy(10, limit_price=p)` and the bar's low is at or below `p`
- **THEN** the order fills at `p`

#### Scenario: Limit buy expires when not crossed
- **WHEN** a strategy emits `ctx.buy(10, limit_price=p)` and the bar's low is above `p`
- **THEN** no fill occurs and the order does not carry to the next bar

#### Scenario: Unaffordable buy skipped
- **WHEN** a strategy emits a buy whose cost exceeds simulated cash
- **THEN** the signal is skipped and the backtest continues

### Requirement: Backtest statistics and equity curve

A completed backtest SHALL record stats: `total_return`, `max_drawdown`, `sharpe` (daily returns, annualized with √252), `win_rate`, `trade_count`, and `final_equity`. It SHALL also persist one equity point per replayed bar (`strategy_equity_points`: run_id, date, equity) where equity = cash + position marked at the bar's close.

#### Scenario: Stats recorded on completion
- **WHEN** a backtest completes
- **THEN** its `stats` contain `total_return`, `max_drawdown`, `sharpe`, `win_rate`, `trade_count`, and `final_equity`

#### Scenario: One equity point per bar
- **WHEN** a backtest replays N bars
- **THEN** N equity points are persisted in chronological order

### Requirement: Backtest failure is explicit

If bar loading fails (no data for symbol/window) or a bar evaluation fails (sandbox timeout/crash), the run SHALL transition to `FAILED` with a human-readable `error` message; partial equity points MAY be retained. A `FAILED` run SHALL NOT appear as `COMPLETED`.

#### Scenario: No data fails the run
- **WHEN** a backtest is requested for a symbol with no bars in the window
- **THEN** the run transitions to `FAILED` with an error naming the symbol

#### Scenario: Sandbox crash fails the run
- **WHEN** a code-track strategy's sandboxed evaluation crashes on a bar
- **THEN** the run transitions to `FAILED` with an error identifying the failing bar

### Requirement: Backtest runs pinned to strategy version

Each backtest run SHALL record the `strategy_version` at start. Editing the strategy afterward SHALL NOT alter the recorded run's version or results.

#### Scenario: Run pins version
- **WHEN** a backtest starts for a strategy at version 3 and the strategy is later edited to version 4
- **THEN** the run still records `strategy_version = 3`

### Requirement: Backtest API and web panel

The system SHALL expose `POST /api/v1/strategies/{id}/backtests` (start), `GET /api/v1/strategy-runs` (list, filterable by strategy/mode/status), and `GET /api/v1/strategy-runs/{id}` (detail including stats and equity curve). The web strategy detail page SHALL provide a backtest panel: window/initial-cash inputs, a stats display, an equity-curve chart, and a documented note on the fill-at-close and ±spread live-divergence assumptions.

#### Scenario: Start and poll a backtest via API
- **WHEN** a client posts a backtest then polls the run detail
- **THEN** the detail eventually shows `COMPLETED` with stats and equity points

#### Scenario: Backtest panel renders results
- **WHEN** a user runs a backtest from the strategy detail page
- **THEN** the page shows the stats and an equity curve chart without a manual refresh loop
