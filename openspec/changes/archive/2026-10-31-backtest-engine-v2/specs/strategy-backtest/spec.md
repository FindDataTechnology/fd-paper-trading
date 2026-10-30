## MODIFIED Requirements

### Requirement: Backtest run over real daily bars

The system SHALL execute a backtest run by replaying historical daily bars (oldest-first) for the strategy's symbol through the same `on_bar(ctx)` evaluation contract used by the live runner. Backtests SHALL run as in-process background tasks with statuses `QUEUED -> RUNNING -> COMPLETED | FAILED`. The backtest request SHALL accept `start`, `end`, and `initial_cash`; `end - start` exceeding the user's group `max_backtest_days` SHALL be rejected. The request SHALL additionally accept optional `cost_model` (commission/tax/slippage configuration, see the cost model requirement), optional `benchmarks` (list of benchmark symbols), and optional walk-forward window parameters (`train_window`/`test_window`/`step`, see the walk-forward capability) — all optional, with backward-compatible defaults.

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

### Requirement: Fill-at-close simulation

The backtester SHALL maintain an in-memory portfolio (cash + signed position) initialized to `initial_cash`. MARKET signals SHALL fill at the signal bar's close. LIMIT buy signals SHALL fill at the limit price if the bar's low is at or below the limit price; LIMIT sell signals SHALL fill at the limit price if the bar's high is at or above the limit price; otherwise the limit signal expires unfilled at that bar's end (documented optimistic assumption, no queue modeling). A buy whose cost exceeds available cash SHALL be skipped (not executed). When a cost model is enabled, the effective fill price SHALL be adjusted by slippage (buy fills at price plus slippage, sell fills at price minus slippage), and commission and stamp tax SHALL be deducted from cash; the recorded cash and equity SHALL reflect these costs.

#### Scenario: Market buy fills at signal-bar close
- **WHEN** a strategy emits `ctx.buy(10)` on a bar with close `c` and sufficient cash
- **THEN** with the cost model disabled the simulated position increases by 10 and cash decreases by `10 * c`

#### Scenario: Limit buy fills when bar low crosses
- **WHEN** a strategy emits `ctx.buy(10, limit_price=p)` and the bar's low is at or below `p`
- **THEN** the order fills at `p`

#### Scenario: Limit buy expires when not crossed
- **WHEN** a strategy emits `ctx.buy(10, limit_price=p)` and the bar's low is above `p`
- **THEN** no fill occurs and the order does not carry to the next bar

#### Scenario: Unaffordable buy skipped
- **WHEN** a strategy emits a buy whose cost exceeds simulated cash
- **THEN** the signal is skipped and the backtest continues

#### Scenario: Slippage and fees move cash and price
- **WHEN** a buy fills with the cost model enabled (slippage 5bps, commission 0.025%)
- **THEN** the fill price is above the signal price by the slippage and cash decreases by quantity times fill price plus commission

### Requirement: Backtest statistics and equity curve

A completed backtest SHALL record stats: `total_return`, `max_drawdown`, `sharpe` (daily returns, annualized with √252), `win_rate`, `trade_count`, and `final_equity`, plus `annual_return` (CAGR), `annual_volatility`, `sortino`, `calmar`, and `turnover` (annualized). It SHALL also persist one equity point per replayed bar (`strategy_equity_points`: run_id, date, equity) where equity = cash + position marked at the bar's close. When the cost model is enabled, the run SHALL also record a cost summary (total commission, total stamp tax, total slippage) and the effective cost configuration snapshot.

#### Scenario: Stats recorded on completion
- **WHEN** a backtest completes
- **THEN** its `stats` contain `total_return`, `max_drawdown`, `sharpe`, `win_rate`, `trade_count`, `final_equity`, `annual_return`, `annual_volatility`, `sortino`, `calmar`, and `turnover`

#### Scenario: One equity point per bar
- **WHEN** a backtest replays N bars
- **THEN** N equity points are persisted in chronological order

#### Scenario: Cost summary recorded when enabled
- **WHEN** a backtest with the cost model enabled completes
- **THEN** the run record contains the cost configuration snapshot and the aggregated commission/tax/slippage amounts
