# add-quant-strategies Tasks

Phasing: §1 (cross-repo) → §2 (data model) → §3–5 (P1: data + template track + backtester) → §6, §9 (P2: conversation + web) → §7–8 (P3: live runner + groups + sandbox hardening). Tests accompany each section.

## 1. Cross-repo: fd-open-data-mcp `read_range` (blocking dependency)

- [x] 1.1 In `/Users/chengsishi/finddata/fd-open-data-mcp`: implement `read_range(session, concept_ids, entity_type, entity_id, start, end) -> {concept_id: [{date, value, source_used}, ...]}` — per ranked source function, one upstream range call, extract every date in `[start, end]`, bulk-write the cache, return the series (design D3)
- [x] 1.2 Add unit tests in fd-open-data-mcp covering: cache hit path, cold-cache upstream fetch + bulk cache write, ranked failover across sources, empty range
- [x] 1.3 Release/bump fd-open-data-mcp and pin the new version in fd-trade's `pyproject.toml`

## 2. Database: schema migration

- [x] 2.1 Alembic migration: create `strategies` (id, user_id FK, name, description, spec JSONB, version int default 1, created_at, updated_at), `strategy_runs` (id, strategy_id FK, strategy_version, mode BACKTEST|PAPER, account_id nullable FK, status, params JSONB, stats JSONB, error, started_at, stopped_at, created_at), `strategy_equity_points` (run_id FK indexed, date, equity Numeric(20,4)), `user_groups` (id, name unique, config JSONB, created_at)
- [x] 2.2 Same migration: add `users.group_id` FK + `users.is_admin` bool (default false); add `orders.strategy_run_id` nullable FK, indexed
- [x] 2.3 Same migration: seed `default` group with default config (max_active_strategies=3, max_backtest_days=730, max_orders_per_day=20, allow_custom_code=false, allowed_symbols=null) and assign all existing users to it
- [x] 2.4 Add SQLAlchemy models for the four new tables and the new columns in `persistence/models.py` + repositories

## 3. Market data: fd-open-data provider (P1)

- [x] 3.1 Add `fd-open-data-mcp` dependency (with data extra) to `pyproject.toml`; `uv sync`
- [x] 3.2 Write `scripts/setup_fd_open_data.py`: run fd-open-data bootstrap (migrate → import-catalog → consume-concepts/propose-bindings → seed-entities) and resolve the curated symbol universe to entity IDs; document in README/.env.example
- [x] 3.3 Implement `src/fd_trade/marketdata/fd_open_data.py`: narrow adapter exposing `get_daily_bars(symbol, start, end) -> list[Bar]` (via `read_range`) and `get_latest_close(symbol) -> Decimal | None`
- [x] 3.4 Implement `FdOpenDataMarketDataProvider(MarketDataProvider)`: `get_bars` (1d only, real bars), `get_quote` (latest close ± 5 bps, $0.01 floor; None on gap), `get_instrument` (SIM equity universe), `search_instruments` (entity resolution + curated fallback); wire `MARKET_DATA_PROVIDER=fd-open-data` selection in app factory
- [x] 3.5 Unit tests for the adapter + provider (mock `read_range`; quote derivation; gap → None; timeframe rejection)

## 4. Strategy core: spec, indicators, templates (P1)

- [x] 4.1 Create `src/fd_trade/strategy/` package: spec Pydantic models (template track + code track, discriminated on `kind`) with full validation errors
- [x] 4.2 Implement `strategy/indicators.py`: pure-Python `sma`, `ema`, `rsi`, `macd`, `bollinger` over oldest-first series + unit tests against known values
- [x] 4.3 Implement `strategy/ctx.py`: the `ctx` contract (bar accessors, indicators, position/cash, buy/sell emitters collecting signals)
- [x] 4.4 Implement `strategy/templates.py`: catalog (sma_cross, rsi_reversion, bollinger_breakout, macd_cross, dca) with per-template JSON Schemas + an interpreter that evaluates a template spec through the same ctx
- [x] 4.5 Unit tests: template param validation (defaults, invalid values, unknown template), each template's entry/exit behavior on synthetic bars

## 5. Backtester (P1)

- [x] 5.1 Implement `strategy/backtester.py`: replay daily bars oldest-first through `on_bar(ctx)`; in-memory portfolio (cash, signed position, avg cost); fill-at-close for market signals; limit fills when bar low/high crosses (expire unfilled); skip unaffordable buys
- [x] 5.2 Compute stats (`total_return`, `max_drawdown`, √252-annualized `sharpe`, `win_rate`, `trade_count`, `final_equity`) and per-bar equity points; persist run + points
- [x] 5.3 API: `POST /api/v1/strategies/{id}/backtests` (start background task, QUEUED→RUNNING→COMPLETED|FAILED, pin strategy_version), `GET /api/v1/strategy-runs` (filters: strategy, mode, status), `GET /api/v1/strategy-runs/{id}` (stats + equity curve); enforce `max_backtest_days`
- [x] 5.4 Tests: unit (portfolio math, limit crossing, stats on synthetic series) + e2e (create strategy → backtest → poll to COMPLETED with stats/points; FAILED paths for no-data and evaluation crash)

## 6. Strategy management API + MCP tools (P2)

- [x] 6.1 API: `POST/GET /api/v1/strategies`, `GET/PATCH/DELETE /api/v1/strategies/{id}` with versioning rules (spec change bumps version; metadata-only does not), user isolation, delete-blocked-while-running (409)
- [x] 6.2 API: `GET /api/v1/strategy-templates` returning the catalog with JSON Schemas
- [x] 6.3 MCP: add `list_strategy_templates`, `create_strategy`, `update_strategy`, `list_strategies`, `get_strategy`, `run_backtest`, `get_strategy_run`, `start_strategy`, `stop_strategy`, `list_strategy_runs` to `mcp/server.py` (surfaced on stdio + HTTP/SSE); structured validation-error messages for LLM self-repair
- [x] 6.4 Update CopilotKit runtime system prompt: strategy persona, template workflow, full ctx contract incl. no-imports rule
- [x] 6.5 Tests: API versioning/isolation/delete-guard; MCP tool round-trips through the REST layer

## 7. Live paper runner (P3)

- [x] 7.1 Implement `strategy/runner.py`: supervised asyncio task per paper run; 30-min poll; new-bar detection (bar date > last processed); evaluate; place signals via `PaperExecutionService` with `strategy_run_id` attribution
- [x] 7.2 State machine + lifecycle: `STARTING→RUNNING→STOPPED|ERROR`; start pins version+account; stop cancels the run's resting LIMITs only (never closes positions); unexpected task death → ERROR with recorded message
- [x] 7.3 Enforcement: `max_active_strategies` on start (409); `max_orders_per_day` per run (skip + log); rejected signals logged, run continues; evaluation failure skips tick
- [x] 7.4 Daily equity snapshot after first tick on a new bar; strategy events (state change, order, rejection, error) persisted and broadcast via `/ws` to the owning user
- [x] 7.5 Orders API: include `strategy_run_id` in responses + filter param
- [x] 7.6 Tests: tick no-op without new bar, evaluation on new bar, order attribution, stop semantics (resting limits cancelled, position kept), daily cap, ERROR on crash, e2e start→tick→order→stop

## 8. User groups + custom-code sandbox (P3)

- [x] 8.1 `FD_ADMIN_EMAILS` bootstrap at registration; admin-only `GET/POST /api/v1/groups`, `PATCH /api/v1/groups/{id}`, `POST /api/v1/users/{id}/group` (403 for non-admins)
- [x] 8.2 Group-limit enforcement helpers (config defaults, resolution) wired into strategy create/update (`allow_custom_code`, `allowed_symbols`), backtest start, run start, runner order placement
- [x] 8.3 Implement `strategy/codegen.py` AST validator: top-level `def on_bar(ctx)` required; no imports; no `_`-prefixed names/attributes; forbidden calls (eval/exec/open/compile/globals/locals/vars/__import__/input/breakpoint); per-violation messages
- [x] 8.4 Implement `strategy/sandbox_worker.py`: JSON stdin job → JSON stdout signals; rlimit CPU 10s / AS 256MB; host-side wall-clock timeout; wire code-track evaluation through it in backtester + runner
- [x] 8.5 Tests: validator accept/reject matrix; sandbox timeout, memory bomb, malformed output; group gate (403 default group, allowed when enabled)

## 9. Web UI (P2, runs after §5/§6 APIs exist)

- [x] 9.1 API client extensions (strategies, templates, backtests, strategy-runs) + types
- [x] 9.2 Strategies list page: name, spec summary, active run status, version; link to detail; auth-gated route
- [x] 9.3 Strategy detail page: template param form (from JSON Schema) / code editor for code track; save shows new version; "newer version available" hint on active runs
- [x] 9.4 Backtest panel: window + initial-cash inputs, stats display, equity-curve chart, fill-at-close/divergence note
- [x] 9.5 Run history table (mode, window/dates, status, headline stats) + live-run status block updating via `/ws` strategy events
- [x] 9.6 Vitest component tests (form rendering from schema, version display, backtest panel states) + Playwright flow (create template strategy → backtest → view curve)

## 10. Final integration

- [x] 10.1 `uv run pytest` green (backend), web Vitest + Playwright green
- [x] 10.2 Update README/CLAUDE.md setup docs (fd-open-data bootstrap, `MARKET_DATA_PROVIDER=fd-open-data`, FD_ADMIN_EMAILS)
- [x] 10.3 Manual smoke: `MARKET_DATA_PROVIDER=fd-open-data` app boot, conversational create→backtest→start→stop via web chat
