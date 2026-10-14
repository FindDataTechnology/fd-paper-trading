# add-quant-strategies Design

## Context

fd-trade today is a manual paper-trading service: REST + WebSocket API, a
React SPA, a CopilotKit chat assistant backed by an MCP server exposing order
tools, and a pure-Python `SimFillEngine` on the order hot path. Account state
(positions, cash, fills) lives in the database, isolated by `account_id`.

Two facts from the current codebase shape this design:

1. **The market-data layer cannot support strategies.** `SIM` emits a static
   quote and a seeded random-walk bar series (backtests would be meaningless);
   the Polygon free-tier provider derives a frozen quote from yesterday's
   close and *falls back to simulated bars* (`polygon.py` delegates
   `get_bars` to the SIM provider). Strategies need real historical daily
   bars and a latest price that actually changes.
2. **The user's own `fd-open-data-mcp`** (PyPI package; local checkout at
   `/Users/chengsishi/finddata/fd-open-data-mcp`) provides real daily data
   (akshare for A-shares, yfinance for US/global) behind a concept/entity
   semantic layer with a read-through cache — but its read API is per-date
   (`read(concept_id, entity_type, entity_id, dates)`), extracting a single
   date from each upstream range fetch. Bulk backtest loads through it would
   be thousands of dispatches.

The system is paper-only by charter (decision D10 in archived changes defers
the live `TradingNode`); this change stays entirely inside that boundary.

## Goals / Non-Goals

**Goals:**

- Versioned strategy entities with a dual-track definition: built-in
  templates (JSON params) and custom Python code (`on_bar(ctx)`).
- One evaluation contract shared by the backtester and the live paper
  runner — a signal computed in backtest is computed identically live.
- Backtests over real historical daily bars with stats + equity curve.
- Live paper execution: daily-bar tick → signals → MARKET/LIMIT orders via
  the existing `PaperExecutionService` (buying-power checks, rate limiting,
  ledgering, and the orders/positions UI all reused unchanged).
- Real daily data integrated as a third `MarketDataProvider` implementation
  (`fd-open-data`) so backtests, live ticks, and manual fills share one price
  world.
- Per-group limits (strategy counts, backtest window, orders/day, custom-code
  gate, symbol allowlist) enforced at the API layer.
- Conversational strategy management via new MCP tools, automatically
  available to both Claude Code and the web chat assistant.
- Web pages to inspect/edit strategies and inspect runs (current + history).

**Non-Goals:**

- Real-money trading or any broker/exchange integration (paper only, forever
  in this change).
- Intraday strategies (minute bars). Daily granularity only in v1.
- Multi-symbol / portfolio-level strategies. A strategy trades exactly one
  symbol in v1.
- Nautilus `BacktestEngine`/`TradingNode` in the strategy path (the v1
  execution-model decision stands: Nautilus matching belongs to the future
  real-venue live node, not the paper hot path).
- Percent-of-equity sizing, short-selling templates, slippage/commission
  modeling, ML signal pipelines.
- Multi-worker live-runner fleet (single-digit strategies per user; in-process
  asyncio tasks suffice).

## Decisions

### D1 — Dual-track strategy spec behind one evaluation contract

A strategy's `spec` (JSONB) is one of:

```jsonc
// Template track
{ "kind": "template", "template": "sma_cross", "symbol": "AAPL",
  "params": { "fast": 10, "slow": 30, "quantity": 10 } }
// Custom-code track
{ "kind": "code", "symbol": "AAPL",
  "source": "def on_bar(ctx):\n    if ctx.rsi(14) < 30:\n        ctx.buy(10)" }
```

Both tracks compile to the same runtime contract: an evaluator callable
invoked once per bar as `on_bar(ctx)`, where `ctx` exposes bar history
(`ctx.closes()`, `ctx.opens()`, …, oldest-first lists ending at the current
bar), computed indicators (`ctx.sma(n)`, `ctx.ema(n)`, `ctx.rsi(n)`,
`ctx.macd(f,s,sig)`, `ctx.bollinger(n,k)`), account state (`ctx.position`,
`ctx.cash`), and order emitters (`ctx.buy(qty, limit_price=None)`,
`ctx.sell(qty, limit_price=None)`). Templates are evaluated by an interpreter
that calls the same ctx methods; custom code *is* the `on_bar` body.

**Why:** the proposal's "user can see and edit strategies in the web"
requirement demands structured editing (a form over template params), which
free-form code can't offer; the "free LLM strategy" requirement demands full
expressiveness. One contract means the backtester and live runner don't know
which track a strategy came from — preserving the backtest↔live parity that
is the whole point of a paper system.

**Alternatives considered:** code-only (rejected: unsafe + not form-editable);
DSL-only (rejected: caps LLM expressiveness); Nautilus `Strategy` subclasses
as the spec format (rejected: drags the engine back into the paper hot path
against the standing execution-model decision, and can't be form-edited
either).

### D2 — `fd-open-data-mcp` consumed as a library, behind a narrow adapter

fd-trade adds `fd-open-data-mcp` as a Python dependency and calls its fetch
layer in-process; no MCP client, no extra long-running server. All usage is
funnelled through `fd_trade/marketdata/fd_open_data.py`, which exposes exactly
two operations upward: `get_daily_bars(symbol, start, end) -> list[Bar]` and
`get_latest_close(symbol) -> Decimal | None`.

**Why:** the strategy backtest/runner needs bulk series loads — a function
call, not a protocol hop. MCP would add process orchestration and serialize
bulk data through a transport for zero isolation benefit (both processes are
ours, same host, same trust domain).

**Alternatives considered:** MCP client to a常驻 `fd-open-data-mcp serve`
process (rejected above); calling akshare/yfinance directly from fd-trade
(rejected: duplicates the ranking/failover/cache semantics the user already
built).

### D3 — `read_range` is added to `fd-open-data-mcp` (cross-repo task)

`fd-open-data-mcp` gains a bulk read:
`read_range(session, concept_ids, entity_type, entity_id, start, end) ->
{concept_id: [{date, value, source_used}, ...]}`. Per ranked source function
it performs **one** upstream range call, extracts every date in `[start, end]`,
bulk-writes the cache, and returns the series — versus today's
one-dispatch-per-date `read`.

**Why:** loading 500 daily bars × 5 concepts through per-date `read` is 2,500
dispatches, each re-pulling the full upstream range on a cold cache. One range
call per concept is the only viable shape, and it is a capability
`fd-open-data-mcp` should own (its `plan_crawl` already sketches the pattern).

**Alternatives considered:** per-date reads accepted as-is (rejected: seconds
→ minutes per backtest load, hammering upstreams); bypassing the concept layer
to call akshare/yfinance directly (rejected in D2).

### D4 — `FdOpenDataMarketDataProvider` unifies the price world

A new `MarketDataProvider` implementation: `get_bars(symbol, "1d", limit)`
serves real historical daily bars via D2's loader; `get_quote(symbol)` returns
the latest cached close ± a small synthetic half-spread (5 bps, $0.01 floor —
same derivation as the Polygon provider); `get_instrument` keeps the SIM
equity universe; `search_instruments` resolves entities through
`fd-open-data-mcp`'s entity identifiers, falling back to the curated universe.
Selected with `MARKET_DATA_PROVIDER=fd-open-data`.

**Why:** the existing provider interface already abstracts exactly this seam.
With one provider feeding backtests (historical bars), live ticks (latest
bar), and order fills (quote), a strategy's backtest price and its live fill
price come from the same series — no silent divergence between "what the
strategy saw" and "what the fill engine used".

**Alternatives considered:** strategies read bars from fd-open-data while
fills stay on SIM/Polygon quotes (rejected: strategy logic and fills would
track different price series — backtests unverifiable against live behavior).

### D5 — Daily-bar evaluation semantics; fill-at-close backtest assumption

Strategies evaluate once per daily bar. The **backtester** replays bars
oldest-first against an in-memory portfolio (`initial_cash` param), filling
signals at the signal bar's close. The **live runner** wakes on a poll
interval (default 30 min), checks whether a new daily bar exists for the
strategy's symbol (new bar date > last processed date), and if so evaluates
and places orders through `PaperExecutionService` — whose fill price is the
provider quote, i.e. that same latest close ± half-spread. Market holidays
fall out naturally: no new bar, no tick.

**Why:** daily bars are what the data layer actually provides (D3/D4), and
retail-style templates (SMA/RSI/Bollinger/MACD/DCA) are daily strategies
anyway. Fill-at-close vs live's close±5bps is a ≤10 bps round-trip divergence,
documented rather than modeled (slippage modeling is a non-goal).

**Alternatives considered:** next-bar-open fills (rejected for v1: live cannot
replicate "tomorrow's open" with a latest-close quote, breaking parity);
intraday aggregation from ticks (rejected: no intraday source).

### D6 — Custom-code safety: AST allowlist + subprocess sandbox + group gate

Three concentric controls:

1. **Validation (synchronous, at create/update):** parse the source; require a
   top-level `def on_bar(ctx)`; forbid `import`/`import from` entirely (the
   host injects everything via `ctx`); forbid names/attributes starting with
   `_`; forbid calls to `eval`, `exec`, `open`, `compile`, `globals`,
   `locals`, `vars`, `__import__`, `input`, `breakpoint`.
2. **Execution (per evaluation):** run in a subprocess
   (`python -m fd_trade.strategy.sandbox_worker`) receiving the job
   (source + bar window + position/cash) as JSON on stdin and returning
   signals as JSON on stdout; `resource.setrlimit` CPU 10 s / address space
   256 MB; wall-clock timeout kills the process; a timeout or crash marks the
   bar evaluation failed (backtest: run FAILED; live: bar skipped, error
   logged to the run).
3. **Authorization:** the custom-code track is disabled by default and enabled
   per group (`allow_custom_code`), so only trusted populations can create
   code strategies at all.

**Why:** even in a paper system, arbitrary code can burn CPU/memory or reach
the internal network. Validation catches accidents and casual abuse; the
subprocess contains hangs and memory bombs; the group gate shrinks the exposed
population to near zero by default.

**Trade-off acknowledged:** v1 has no OS-level network isolation inside the
subprocess — the no-imports rule blocks the obvious vectors (`socket`,
`urllib`, `requests` are unreachable without imports), but a determined
attacker with a CPython sandbox-escape bug is out of scope for a paper
system behind a default-off group gate. Documented as a limitation.

### D7 — Group-based limits, enforced at the API layer

`user_groups(id, name, config JSONB)` with keys:
`max_active_strategies` (concurrent PAPER runs per user, default 3),
`max_backtest_days` (default 730), `max_orders_per_day` (per strategy run,
default 20), `allow_custom_code` (default false), `allowed_symbols`
(default null = all). `users.group_id` points at one group; a migration seeds
a `default` group and assigns all existing users to it. Enforcement points:
create/start PAPER run (active-count), backtest request (window clamp →
reject beyond limit), runner order placement (daily count, signal skipped +
event logged), strategy create/update (code gate, symbol allowlist).

Admin management: `users.is_admin` flag (new column); `GET/POST/PATCH
/api/v1/groups`, `POST /api/v1/users/{id}/group` require it. First admin is
created by env (`FD_ADMIN_EMAILS` on register) or direct DB update.

**Why group-level only:** "special-case one user" = put them in a one-person
group. A parallel per-user override layer adds a second resolution path for
no v1 use case.

### D8 — Run model and state machines

`strategy_runs` pins `strategy_version` at start: editing a strategy bumps its
version but never mutates a live run's logic; the UI surfaces "newer version
available, restart to apply". Backtest runs: `QUEUED → RUNNING → COMPLETED |
FAILED`, executed as an in-process background task (thousands of bars × pure
Python = sub-second to seconds; no job queue). Paper runs: `STARTING →
RUNNING → STOPPED | ERROR`; stopping cancels that run's resting LIMIT orders
(via the existing cancel path) and never closes positions (positions are
account property; the user decides in the positions UI). Each run writes
`strategy_equity_points` — backtest: one point per replayed bar; paper: a
daily snapshot (cash + positions marked at latest close). Strategy events
(order placed, run state change, error) broadcast over the existing `/ws`
manager as new event types so the web UI updates live.

**Why pin versions:** conversationally editing a strategy must not silently
change a running system's behavior mid-flight; "restart to apply" is explicit
and matches how the chat flow narrates changes.

### D9 — MCP tools as the single conversational surface

New tools on the existing fd-trade MCP server: `list_strategy_templates`
(returns template catalog + JSON Schemas so the LLM can fill params),
`create_strategy`, `update_strategy`, `list_strategies`, `get_strategy`,
`run_backtest`, `get_strategy_run`, `start_strategy`, `stop_strategy`,
`list_strategy_runs`. Both MCP transports (stdio for Claude Code, HTTP/SSE
for the CopilotKit runtime) expose them with no runtime changes beyond the
system prompt. Validation errors are returned as structured tool errors so
the LLM can repair its own output in-loop.

**Why:** the chat assistant and Claude Code already share this MCP server —
tools added once appear in both conversation surfaces for free. The CopilotKit
runtime prompt gains a strategy-building persona and the ctx-API contract for
the code track.

### D10 — Data model

```
strategies         id, user_id, name, description, spec JSONB, version int,
                   created_at, updated_at
strategy_runs      id, strategy_id, strategy_version, mode BACKTEST|PAPER,
                   account_id (required for PAPER; null for BACKTEST),
                   status, params JSONB (start/end/initial_cash),
                   stats JSONB, error, started_at, stopped_at, created_at
strategy_equity_points  run_id (FK, indexed), date, equity Numeric(20,4)
user_groups        id, name unique, config JSONB, created_at
users              + group_id FK, + is_admin bool
orders             + strategy_run_id FK nullable, indexed
```

Backtest stats: `total_return`, `max_drawdown`, `sharpe` (daily,
annualized √252), `win_rate`, `trade_count`, `final_equity`. One alembic
migration creates/alters all of the above (the repo's established mechanism).

### D11 — Built-in template catalog (v1)

All single-symbol, long-only, fixed-quantity sizing; params validated by
per-template JSON Schema:

| Template | Params (defaults) | Entry | Exit |
|---|---|---|---|
| `sma_cross` | fast(10), slow(30), quantity | fast crosses above slow | cross below |
| `rsi_reversion` | period(14), oversold(30), overbought(70), quantity | RSI < oversold | RSI > overbought |
| `bollinger_breakout` | period(20), std_mult(2.0), quantity | close > upper band | close < mid band |
| `macd_cross` | fast(12), slow(26), signal(9), quantity | MACD crosses above signal | cross below |
| `dca` | interval_bars(5), quantity | buy every N bars | never |

Indicators (`sma`, `ema`, `rsi`, `macd`, `bollinger`) are implemented once in
pure Python (`strategy/indicators.py`) and shared by templates and the custom
`ctx` — one implementation, no divergence.

## Risks / Trade-offs

- **`read_range` is cross-repo work** in `fd-open-data-mcp`; fd-trade cannot
  merge it unilaterally → It is task 1.1 and the explicit first dependency of
  everything downstream; the fd-trade adapter is written against its contract
  with a thin seam, so a contract change is contained in one file.
- **fd-open-data bootstrap burden** (migrate + import-catalog + consume/propose
  + seed-entities before first read) → A `scripts/setup_fd_open_data.py`
  automates the sequence and resolves the curated symbol universe to entity
  IDs; setup docs updated. Without it the provider starts in "no data" mode
  and `get_quote` returns `None` (existing data-gap semantics).
- **Sandbox escape / resource abuse from custom code** → D6's three layers;
  `allow_custom_code` defaults false so the exposed population is opt-in per
  group. Residual risk (CPython escapes, no network namespace) documented.
- **Live runner shares the app process**; a hung strategy could eat a worker
  → group caps keep counts tiny (default 3/user); custom code runs in a
  subprocess with hard CPU limits; template evaluation is bounded arithmetic;
  runner ticks are independent asyncio tasks supervised with restart-on-error
  (run → ERROR state, never silently dead).
- **Daily granularity disappoints users expecting intraday** → documented in
  the chat persona and UI copy; intraday requires a new data source and is a
  listed non-goal.
- **LLM generates invalid specs/code in conversation** → tools return
  structured validation errors designed for LLM self-repair; templates cover
  the common cases so free code is the exception path.
- **Backtest↔live divergence** (close vs close±spread; resting-limit behavior
  differs: backtest fills limit orders only if the bar's range crosses)
  → documented in the backtest panel; bounded at ~10 bps round-trip for
  market orders.
- **fd-open-data upstream flakiness** (akshare/yfinance rate limits) →
  inherited ranked failover + read-through cache; the live runner tolerates a
  missing bar (skips the tick, retries next poll).

## Migration Plan

1. In `fd-open-data-mcp`: implement + release `read_range`; bump fd-trade's
   dependency pin.
2. fd-trade: alembic migration for D10 tables/columns + default group seed.
3. Deploy behind config: `MARKET_DATA_PROVIDER=fd-open-data` only after
   `setup_fd_open_data.py` has run; SIM/Polygon remain selectable, so rollback
   = revert env var (strategy tables are inert without the provider).
4. Custom-code track ships dark: `allow_custom_code=false` in the default
   group; enable per group when sandbox confidence is established.

## Open Questions

- Exact `read_range` signature/semantics (final say belongs to the
  fd-open-data-mcp repo; D3 is fd-trade's required contract).
- Symbol-universe mapping policy: fd-trade's curated 20-symbol SIM list as the
  v1 allowlist, or open entity resolution (any yfinance/akshare symbol)?
  Leaning: curated list for templates UI, entity resolution for search.
- Whether backtest LIMIT orders should model queue position (v1: fill iff the
  bar's low/high crosses the limit — optimistic, documented).
- Equity-snapshot time of day for paper runs (v1: first tick after a new bar
  is detected, i.e. snapshot follows evaluation).
