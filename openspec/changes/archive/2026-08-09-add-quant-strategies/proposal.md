# add-quant-strategies Proposal

## Why

Today the platform only supports discretionary trading: manual order entry and
chat-placed single orders. There is no way to express a systematic strategy
once and let it trade repeatedly, no way to validate an idea against history
before committing (paper) capital, and no record of how a strategy performed.
Users have asked for exactly that loop: **build a strategy in conversation →
backtest it → run it live in a paper account → watch and revise it from the
web UI**.

A hard blocker hides underneath: the current market-data providers cannot
support strategies at all. The SIM provider synthesizes a seeded random walk
(backtests would be meaningless) and the Polygon free-tier provider returns a
frozen previous-close quote with simulated bars (live strategies would never
trigger). Real daily bars from a real source are a prerequisite, not a nice-to-have.

## What Changes

- **Strategy domain model**: `strategies` (versioned, per-user), `strategy_runs`
  (`BACKTEST` | `PAPER` mode, status lifecycle, aggregate stats),
  `strategy_equity_points` (curve series), and `orders.strategy_run_id` attribution.
- **Dual-track strategy definition**:
  - *Template track* — a JSON spec referencing a built-in template
    (SMA crossover, RSI mean-reversion, Bollinger breakout, MACD crossover, DCA)
    plus validated parameters; editable as a structured form in the web UI.
  - *Custom-code track* — LLM- or user-authored Python implementing
    `on_bar(ctx)`, gated by AST allowlist validation and executed in a
    resource-limited subprocess sandbox.
  - Both tracks share one `evaluate(bars, position) → signals` contract so the
    backtester and the live runner execute identical logic.
- **Backtester**: replays real daily bars over a date window, simulates
  cash/positions in memory (fills at signal-bar close, documented assumption),
  emits stats (total return, max drawdown, win rate, Sharpe, trade count) and a
  daily equity curve.
- **Live paper runner**: strategies bound to a paper account tick on daily
  bars; signals become MARKET or LIMIT orders through the existing
  `PaperExecutionService` (rate limits, buying-power checks, ledgering all
  reused). Orders and fills are attributed to the run and visible in the
  existing orders UI.
- **New market-data provider `fd-open-data`**: backed by the `fd-open-data-mcp`
  package (imported as a library), implementing the existing
  `MarketDataProvider` interface — real historical daily OHLCV bars (akshare
  for A-shares, yfinance for US) and latest-close-derived quotes, so backtests,
  live ticks, and manual order fills all see one consistent price world.
  Requires adding a bulk `read_range` API to `fd-open-data-mcp` (its current
  per-date `read` makes bulk backtest loads impractical).
- **User groups with per-group limits**: `user_groups` (config JSONB:
  `max_active_strategies`, `max_backtest_days`, `max_orders_per_day`,
  `allow_custom_code`, `allowed_symbols`); every user belongs to one group
  (default group out of the box); limits enforced at the API layer.
- **Conversational strategy management**: new MCP tools
  (`list_strategy_templates`, `create_strategy`, `update_strategy`,
  `run_backtest`, `start_strategy`, `stop_strategy`, `list_strategy_runs`,
  `get_strategy_run`) — automatically available both to Claude Code (existing
  fd-trade MCP server) and to the web chat assistant (CopilotKit runtime
  consumes the same MCP server).
- **Web UI**: strategies list page + strategy detail page (template parameter
  form, code editor for the custom track, backtest panel with stats + equity
  chart, run history table, live-run status).

## Capabilities

### New Capabilities

- `strategy-management`: strategy CRUD with versioning, dual-track spec
  (template/custom-code) validation, template catalog, strategy web pages.
- `strategy-backtest`: historical backtest execution over daily bars, result
  stats and equity curves, backtest run records.
- `strategy-live-execution`: paper live runner — daily-bar tick loop, signal →
  order placement through the execution service, run state machine, run
  monitoring and history.
- `strategy-conversation`: MCP tool surface and chat-assistant integration for
  building, backtesting, and controlling strategies conversationally.
- `strategy-sandbox`: AST allowlist validation and sandboxed subprocess
  execution of custom strategy code (resource limits, no network, data only via
  the injected `ctx`).
- `user-groups`: group definitions carrying per-group strategy limits, user
  group membership, and limit enforcement.

### Modified Capabilities

- `market-data`: provider selection gains `fd-open-data`; adds requirement for
  real historical daily bars and latest-close quotes from that provider.
- `order-execution`: orders gain optional strategy-run attribution
  (`strategy_run_id`) exposed in order APIs and used for run history.

## Impact

- **New backend package**: `src/fd_trade/strategy/` (spec schemas, template
  catalog + evaluator, backtester, live runner, code validator + sandbox) and
  `src/fd_trade/marketdata/fd_open_data.py` (provider + bulk bar loader).
- **APIs**: `/api/v1/strategies*`, `/api/v1/strategies/{id}/backtests`,
  `/api/v1/strategy-runs*`, `/api/v1/groups*` (admin); orders responses gain
  `strategy_run_id`.
- **Database**: new tables `strategies`, `strategy_runs`,
  `strategy_equity_points`, `user_groups`; new columns `users.group_id`,
  `orders.strategy_run_id`.
- **Dependencies**: `fd-open-data-mcp` (PyPI) added; its one-time catalog
  bootstrap (`migrate`, `import-catalog`, `seed-entities`) becomes part of
  environment setup. Heavy upstream data deps (akshare/yfinance) ride along as
  its `data` extra.
- **External repo**: `fd-open-data-mcp` gains a `read_range` bulk-read API
  (tracked as a task here, implemented in that repo).
- **MCP/chat**: `src/fd_trade/mcp/server.py` + `http_server.py` gain strategy
  tools; CopilotKit runtime prompt updated.
- **Web**: `web/src/pages/StrategiesPage.tsx`,
  `StrategyDetailPage.tsx`, strategy components, API client extensions.
- **Ops**: live runner runs inside the FastAPI app process (asyncio tasks,
  single-digit strategies per user by group limit); no worker fleet in v1.
- **Phasing**: P1 = fd-open-data integration + data model + template track +
  backtester; P2 = conversational building + web strategy pages; P3 = live
  runner + group limits + custom-code sandbox.
