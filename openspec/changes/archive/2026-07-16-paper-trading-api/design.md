## Context

fd-trade is greenfield: only `CLAUDE.md` and the `openspec/` scaffold exist. Per `CLAUDE.md`, the project is built on **Nautilus Trader** and the paper-trading half is "a TradingNode pointed at a sandbox/testnet." This change delivers that half as a **multi-tenant service**: many users, each with multiple paper accounts, placing stock orders through a REST/WebSocket API, filled by a simulated engine against real-time market data - no broker account, no real capital.

User-confirmed decisions for this change:
- **Execution**: simulated fills (no real broker).
- **Market data**: real-time feed (real stock prices).
- **Stack**: FastAPI + PostgreSQL.

Nautilus specifics verified via Context7 (current `develop` docs):
- `nautilus-sandbox` provides a **simulated execution client** backed by an `OrderMatchingEngine` that executes against live market data - explicitly intended for paper trading without external exchanges.
- **Databento** is a stock market-data adapter with no execution client; it is designed to be paired with the sandbox for simulated execution. (Interactive Brokers also offers a paper port with `DELAYED_FROZEN` data, but that reintroduces a broker dependency the user chose to avoid.)
- Orders are submitted from a `Strategy` via `self.order_factory.<market|limit>(...)` + `self.submit_order(order)`.
- A `TradingNode` is configured with `add_data_client_factory(...)` + `add_exec_client_factory(...)`, then `build()` + `run()`.

## Goals / Non-Goals

**Goals:**
- Multi-user authentication: each user is isolated and can call the API via JWT or API token.
- Multi-account: a user owns N paper accounts, each with isolated cash, positions, and order history.
- API-driven order placement: place and cancel market/limit stock orders programmatically; query orders, positions, and account state.
- Simulated execution against real-time market data, via Nautilus `nautilus-sandbox`, with no real capital and no broker.
- Durable persistence of users, accounts, orders, positions, and fills in PostgreSQL.
- Live market-data streaming to API clients over WebSocket.
- Repo bootstrap (`uv`, `pyproject.toml`, layout, `pytest`) per `CLAUDE.md`.

**Non-Goals:**
- Real-money / live-broker trading (out of scope; this is paper only).
- The AI quant subsystem and signal generation (separate; will feed signals into these accounts in a later change).
- Automated/algorithmic strategy logic - this change delivers API-driven (manual/programmatic) order entry, not signal-driven strategies.
- Advanced order types beyond market and limit (stop, trailing, OCO deferred).
- A web UI - the contract is the REST/WebSocket API.
- Cross-account or cross-user aggregation/portfolio management.

## Decisions

### D1. Execution: `nautilus-sandbox` simulated execution client
Use Nautilus's sandbox execution client (OrderMatchingEngine) to fill paper orders against the live market-data feed. **Why over alternatives**: the user chose simulated fills; the sandbox is purpose-built for "paper trading in real-time without actual order execution on external exchanges," so we get realistic fill semantics (market fills at quote, limit fills on touch) without a broker. Alternative considered: `BacktestEngine` (rejected - historical replay, not live). Alternative considered: a custom hand-rolled matching engine (rejected - reinvents what the sandbox provides and diverges from the Nautilus model the project committed to).

### D2. Market data: Databento (primary), provider behind an interface
Ingest real-time stock data via the Databento adapter, which is explicitly designed to pair with the sandbox for simulated execution. Wrap provider access behind a thin data-client interface so Polygon/Alpaca can substitute later. **Why**: Databento covers US equities with a clean Nautilus adapter and no execution coupling. Alternative: Interactive Brokers `DELAYED_FROZEN` (rejected - requires a broker gateway and account). Alternative: Polygon (kept as a swap-in option via the interface).

**Update (implementation):** `PolygonMarketDataProvider` is the implemented real provider - the user supplied a free-tier Polygon key. The free tier is not entitled to real-time NBBO/last-trade (HTTP 403), so the provider uses the previous-close aggregate (real price, end-of-day, not intraday) and synthesizes a small spread. Provider is selected via `MARKET_DATA_PROVIDER` (`simulated` | `polygon`). Databento (real-time) remains a future swap-in behind the same interface.

### D3. Multi-account isolation: per-account simulated Venue in a shared TradingNode
Run **one shared `TradingNode`** hosting the (single) Databento data client and the sandbox execution client. Each paper account maps to a **dedicated simulated `Venue`** (e.g. `PAPER-{account_id}`) inside the sandbox, giving per-account isolation of balances, positions, and order books while sharing one market-data feed. **Why**: market data is global (same prices for everyone), so a single data subscription fan-out is efficient; per-account venues keep account state isolated in Nautilus. Alternative considered: one `TradingNode` per account (rejected at v1 - process/event-loop overhead does not scale to many accounts; revisit via sharding if needed). Alternative considered: simulate entirely in Postgres/app code (rejected - loses Nautilus's fill model and the project's stated foundation).

### D4. API <-> Nautilus bridge: async command/response queues
FastAPI/Uvicorn and the Nautilus event loop are separate async contexts; `submit_order` must run on the Nautilus loop. The API enqueues an immutable **order command** onto a thread-safe/asyncio queue; a bridging `Strategy` (one per account, or a single multiplexing strategy) drains the queue on the Nautilus loop, calls `submit_order`, and emits fill/order events back through a **response channel** that the API awaits. **Why**: keeps loop affinity correct and avoids direct cross-loop calls. Alternative: direct synchronous calls (rejected - unsafe across loops). Reads (positions, orders) are served from the Nautilus **cache** for low latency and reconciled with Postgres.

### D5. Durable state: PostgreSQL is source of truth; Nautilus cache is live state
Postgres persists users, accounts, orders, positions, and fills. Every order and fill event is written to Postgres as it occurs (event-sourced append for fills). On restart, account state (balances, open positions, open orders) is **rebuilt from Postgres into the Nautilus cache** before the node resumes. **Why**: durability and recoverability; Nautilus is treated as the live execution cache, not the system of record. Alternative: Nautilus as sole source of truth (rejected - hard to query/audit at scale and risky across restarts).

### D6. API surface: FastAPI REST + WebSocket, `/api/v1` versioned
REST endpoints for auth, accounts, orders, positions; WebSocket for live market-data streaming and order/fill updates. **Why**: FastAPI gives async + Pydantic validation + automatic OpenAPI; WebSocket suits real-time ticks. Versioning under `/api/v1` so future changes are non-breaking.

### D7. Auth: JWT access tokens + revocable API tokens; bcrypt
Short-lived JWT access tokens for interactive sessions; long-lived, revocable **API tokens** for programmatic/algorithmic order placement (the user's "make an order through the API" use case). Passwords hashed with bcrypt; secrets in env vars via `python-dotenv`. **Why**: API tokens are the natural fit for automated trading clients and are easier to rotate/revocate than JWTs.

### D8. Persistence layer: SQLAlchemy 2.0 async + asyncpg + Alembic
Async ORM over `asyncpg` for compatibility with FastAPI's async loop; Alembic for schema migrations. **Why**: mature, async-native, well-known. Alternative: SQLModel (acceptable; deferred - SQLAlchemy 2.0 is more flexible for the domain).

### D9. Instrument scope: US equities (NASDAQ/NYSE)
Instruments are loaded into the Nautilus cache via the data client's instrument provider on startup/subscription. `InstrumentId` uses the provider's convention (e.g. `"AAPL.NASDAQ"`). **Why**: matches "stock" ask and the chosen data providers' coverage.

### D10. v1 execution: stateless Nautilus fill calculator + Postgres ledger (IMPLEMENTATION REFINEMENT)
The v1 implementation departs from D1/D3/D4/9.1's live `TradingNode` + `SandboxLiveExecClientFactory` + async command-queue bridge. Instead it uses Nautilus's `BacktestEngine` (the same `SimulatedExchange`/`OrderMatchingEngine` that `SandboxExecutionClient` wraps) as a **stateless per-order fill calculator** on a canonical `SIM` venue, and keeps the multi-account ledger (positions, balances, fills) in PostgreSQL. Per order: fetch quote from the market-data provider -> compute the fill with the Nautilus matching engine -> apply the fill to the Postgres ledger. **Why the deviation**: the spike (6.1) found that (a) Nautilus's global Rust logger can only be initialized once per process, so only one `BacktestEngine`/`TradingNode` can exist - making the live-node + dynamic-per-account-venue model impractical to run reliably alongside FastAPI, and (b) hyphenated per-account venue names break the backtest account issuer. The stateless-calculator approach uses the **same Nautilus matching engine** for correct market/limit fill semantics, is fully testable (31 tests, no live feed needed), and delivers the user's core ask (API order placement with simulated fills, multi-account, multi-user). Account isolation is by `account_id` in Postgres; orders are computed independently so they cannot cross-match. The `BacktestEngine` is created with `bypass_logging=True` per fill (no logger re-init). **Deferred to a later phase**: the live `TradingNode` (9.1), real-time market-data streaming over WebSocket (8.4) and the upstream subscription fan-out (5.3), the Databento data client (5.1 real impl), and the async command-queue bridge (7.1) - these remain the intended target for real-time, signal-driven operation. The current `MarketDataProvider` interface and `PaperExecutionService` are structured so the live-node path can replace the simulated calculator without changing the API.

## Risks / Trade-offs

- **[nautilus-sandbox multi-account support - RESOLVED by spike]** The sandbox exec client is per-venue: `SandboxExecutionClientConfig(venue=..., starting_balances=[...], account_type='CASH')` registered via `SandboxLiveExecClientFactory`. A `TradingNode` supports multiple venues per instance, so per-account simulated venues coexist in one node (verified against Nautilus 1.230.0; two independent per-account configs constructed). No one-node-per-account fallback is needed for v1 correctness. -> Two refinements now feed tasks 5.x/6.x: (a) each account also needs its own instrument copies (`SYMBOL.PAPER-{id}`) because a venue's matching engine only matches instruments on that venue - the shared market-data feed must be fanned out and **republished to each account's venue**; (b) see the new lifecycle risk below.
- **[Dynamic account lifecycle vs node build-time registration - NEW]** Accounts are created dynamically via the API (`POST /api/v1/accounts`), but a `TradingNode` registers exec client factories at `build()` time (before `run()`). Adding a new per-account venue/exec client to a *running* node is not the standard pattern. -> Mitigation: investigate whether the live execution engine accepts a registered client after `build()`; if not, choose between (i) a pool of pre-provisioned venues assigned to new accounts, (ii) one `TradingNode` per account (simpler lifecycle, worse scale), or (iii) node rebuild on account creation. This decision is made in the execution-layer implementation phase (tasks 6.2+); the API/router abstraction (D3/D4) is designed so the choice does not leak to clients.
- **[FastAPI<->Nautilus loop bridging complexity]** Cross-loop command/response adds latency and failure modes (e.g., node stops draining). -> Mitigation: thin, well-tested queue adapter; per-command timeouts; API returns `202 Accepted` with an order reference and clients poll/subscribe for fill state.
- **[Dual-state drift (Postgres vs Nautilus cache)]** Two copies of positions/orders can diverge on partial failures. -> Mitigation: Postgres is source of truth; append every fill event transactionally; reconcile cache from Postgres on restart and on a periodic health check.
- **[Market-data feed cost / rate limits]** Real-time data providers bill per symbol/stream. -> Mitigation: subscribe once per symbol server-side and fan out to all interested clients/accounts; enforce per-user symbol-subscription limits.
- **[Provider outage / data gaps]** No live quote => fills are undefined. -> Mitigation: when no recent quote for a symbol, reject new market orders with a clear error; limit orders queue and fill on next valid quote.
- **[Single shared node is a blast radius]** One node crash affects all accounts. -> Mitigation: durable Postgres state enables fast restart/recovery; design the router so the node can be sharded later (D3 fallback) without API changes.
- **[Simulated fills are not broker-realistic]** No rejection, no partial fills from liquidity constraints beyond the matching engine's model. -> Mitigation: accepted trade-off for paper trading; documented in API responses.

## Migration Plan

Greenfield - no existing data or API to migrate.

**Rollout sequence:**
1. Bootstrap repo (`uv init`, `pyproject.toml`, deps, layout, `pytest`).
2. Provision PostgreSQL + obtain market-data provider credentials (env vars).
3. Build bottom-up: `user-auth` -> `paper-accounts` -> `market-data` -> `order-execution` -> `trading-api`.
4. Account-isolation spike on `nautilus-sandbox` (validates D3 / the top risk) before full execution implementation.
5. Integration tests against sandbox + live data feed (or recorded data replay).
6. Run locally; smoke-test the full order lifecycle via the API.

**Rollback:** Stop the service. No data to migrate; account state lives in Postgres and can be inspected/reset independently. Secrets never enter the repo.

## Open Questions

- **Market-data provider selection** (Databento vs Polygon vs Alpaca): decide during implementation based on cost, US-equity coverage, and Nautilus adapter maturity. Interface (D2) keeps this swappable.
- **Single node vs sharded pool**: defer sharding until account volume is characterized; the router (D3/D4) is designed so a pool can replace a single node without API changes.
- **Sandbox per-account venue support - RESOLVED**: the spike (task 6.1) confirmed `nautilus-sandbox` isolates per-account venues natively via per-venue `SandboxExecutionClientConfig` + `SandboxLiveExecClientFactory` in one `TradingNode` (Nautilus 1.230.0). No one-node-per-account fallback required for correctness.
- **Dynamic account -> venue lifecycle - PARTIALLY RESOLVED**: `LiveExecutionEngine` exposes `register_client` / `deregister_client` / `registered_clients`, indicating per-account sandbox exec clients can be registered onto a *running* node's execution engine (not only at `build()`). Task 6.2 should verify creating a `SandboxExecutionClient` against the running node's msgbus/cache/clock/portfolio and calling `engine.register_client(...)`; if that works, dynamic account creation maps cleanly to runtime venue registration (no pool/rebuild/one-node-per-account needed).
- **Order query latency budget**: whether position/order reads must be cache-only (<10ms) or can tolerate a Postgres round-trip - informs how aggressively to keep cache and DB reconciled (D5).
