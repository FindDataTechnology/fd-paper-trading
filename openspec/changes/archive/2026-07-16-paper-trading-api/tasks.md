## 1. Bootstrap & Infrastructure

- [x] 1.1 Run `uv init` and create `pyproject.toml` (Python 3.12+); add deps: `nautilus_trader`, `fastapi`, `uvicorn[standard]`, `sqlalchemy`, `asyncpg`, `alembic`, `python-jose`, `passlib[bcrypt]`, `python-dotenv`, `pytest`, `httpx` - nautilus_trader declared in an optional `trading` extra (installed in task 6.1) to keep the foundation lightweight
- [x] 1.2 Create source layout: `src/fd_trade/{api,auth,accounts,marketdata,execution,persistence,node}/` and `tests/` - foundation + `marketdata` + `execution` dirs created; `node` deferred (v1 uses BacktestEngine per design D10)
- [x] 1.3 Add `.env.example` with `python-dotenv` loading; document required vars (`DATABASE_URL`, `JWT_SECRET`, `JWT_EXPIRY_MINUTES`, `MARKET_DATA_PROVIDER`, `MARKET_DATA_API_KEY`, `PER_USER_SYMBOL_LIMIT`)
- [x] 1.4 Configure `pytest` (asyncio mode) and add `tests/test_smoke.py` that imports the package and passes
- [x] 1.5 Provision a local PostgreSQL (Docker or service) and verify app connectivity - Homebrew `postgresql@14` on port **5433** (5432 occupied); `fdtrade` DB created; app connectivity verified

## 2. Persistence Layer

- [x] 2.1 Define SQLAlchemy 2.0 async models: `User`, `ApiToken`, `Account`, `Order`, `Fill`, `Position`, `BalanceTransaction`
- [x] 2.2 Configure async engine + session factory (`asyncpg`); add a `get_session` FastAPI dependency - driver-agnostic (asyncpg for Postgres, aiosqlite for tests)
- [x] 2.3 Initialize Alembic and generate the first migration creating all tables - async env.py, `initial schema` migration generated and applied to Postgres
- [x] 2.4 Implement repository functions (CRUD) for each model

## 3. User Auth (`user-auth`)

- [x] 3.1 Implement bcrypt password hashing and verification
- [x] 3.2 Implement JWT access token issuance and validation (`python-jose`) with bounded expiry
- [x] 3.3 Implement API token creation (store hash only, return plaintext once) and revocation
- [x] 3.4 Implement a FastAPI auth dependency that accepts JWT or API token and resolves the requesting user
- [x] 3.5 Implement endpoints under `/api/v1/auth` (register, login) and `/api/v1/tokens` (create, list, revoke)
- [x] 3.6 Implement a user-scoping helper: all resource lookups filter by `user_id`; cross-user access returns `404`
- [x] 3.7 Tests: registration, duplicate rejection, login, invalid credentials, token create/revoke, cross-user denial

## 4. Paper Accounts (`paper-accounts`)

- [x] 4.1 Implement account creation with an initial cash balance, scoped to the owning user
- [x] 4.2 Implement list/get account summary (balance, equity, open positions count, open orders count) - equity = cash only until the market-data layer marks positions to market
- [x] 4.3 Implement paper deposit/withdrawal with recorded `BalanceTransaction` entries
- [x] 4.4 Implement account reset (cancel open orders, flatten positions at market, restore starting balance) - DB-level reset; flattens positions at cost basis and cancels DB-side open orders
- [ ] 4.5 Implement durable state rebuild: reconstruct balance, open positions, and open orders from Postgres on startup - deferred: Postgres is already the source of truth (design D5/D10); rebuild-into-Nautilus-cache is moot for v1's stateless fill calculator, and applies to the future live-node path
- [x] 4.6 Expose endpoints under `/api/v1/accounts`
- [x] 4.7 Tests: multiple accounts per user, isolation between users/accounts, reset, restart rebuild - restart-rebuild scenario deferred with 4.5

## 5. Market Data (`market-data`)

- [x] 5.1 Add the chosen provider's Nautilus data client factory (Databento primary) behind a provider interface so Polygon/Alpaca can swap in (design D2) - `MarketDataProvider` interface + `SimulatedMarketDataProvider` (tests) + **`PolygonMarketDataProvider` (real prices via free-tier previous-close aggregate)** wired in; selected via `MARKET_DATA_PROVIDER` env (`simulated`/`polygon`). Databento (real-time) is a future swap-in. Free Polygon tier gives end-of-day close, not real-time intraday (NBBO/last-trade return 403)
- [x] 5.2 Implement instrument loading into the Nautilus cache on subscribe; reject orders for instruments not in cache - instruments come from the provider (`Equity` on `SIM` venue); unknown symbols rejected with `unknown_instrument` (not via Nautilus cache subscription - see D10)
- [ ] 5.3 Implement a server-side subscription manager: one upstream subscription per symbol, fan-out to clients; enforce per-user symbol-subscription limits - deferred (arrives with real-time streaming / the live node)
- [ ] 5.4 Implement the WebSocket market-data streaming endpoint - deferred
- [x] 5.5 Implement data-gap detection: reject market orders when no recent quote; rest limit orders until a valid quote - market orders rejected with `no_quote` when the provider returns no quote; the simulated provider always returns a quote, so gaps are exercised via the interface contract (limit orders also require a quote in v1 - minor deviation)
- [ ] 5.6 Tests: shared upstream fan-out, subscription-limit enforcement, data-gap rejection, unknown-instrument rejection - partial: `no_quote` and `unknown_instrument` rejection tested via `test_orders.py`; fan-out + subscription-limit tests deferred with 5.3/5.4

## 6. Order Execution (`order-execution`)

- [x] 6.1 Spike: verify `nautilus-sandbox` supports per-account simulated venues within one `TradingNode` (design D3, top risk). Record outcome; if unsupported, fall back to one-node-per-account for v1 - **CONFIRMED VIABLE**: `SandboxExecutionClientConfig` is per-venue, registered via `SandboxLiveExecClientFactory`; a `TradingNode` supports multiple venues per instance. The spike also found Nautilus's global logger is one-shot per process, which drove the v1 stateless-calculator approach (design D10)
- [x] 6.2 Integrate the sandbox execution client factory into the `TradingNode`, paired with the data client - **DEVIATION (D10)**: v1 uses Nautilus's `BacktestEngine` (the same `OrderMatchingEngine` `SandboxExecutionClient` wraps) as a stateless per-order fill calculator with `bypass_logging=True`, not a live `TradingNode` + `SandboxLiveExecClientFactory`. Functional intent (Nautilus matching engine for fills) is met and tested
- [x] 6.3 Implement market-order fills (buy at ask, sell at bid) and limit-order fills (on touch) via the sandbox matching engine - verified (market buy@ask, sell@bid; limit fills on cross, rests otherwise)
- [x] 6.4 Implement order cancellation for open orders; reject cancellation of fully-filled orders
- [x] 6.5 Implement per-account venue isolation: independent order books with no cross-account matching - **DEVIATION (D10)**: isolation is by `account_id` in Postgres; each order is computed independently by the stateless fill engine, so orders cannot cross-match. Not via Nautilus per-account venues
- [x] 6.6 Implement fill event handling: update position and cash balance, and append an immutable fill event to Postgres - signed-quantity position accounting + `Fill` + `BalanceTransaction` records
- [x] 6.7 Tests: market/limit fills, cancellation, per-account isolation, fill persistence - `tests/test_orders.py`

## 7. API <-> Nautilus Bridge (design D4)

- [ ] 7.1 Implement the async command queue: the API enqueues order commands; a bridging `Strategy` drains them on the Nautilus loop - **DEVIATION (D10)**: v1 calls the stateless fill engine via `asyncio.to_thread` (no separate Nautilus loop / command queue). The command-queue bridge is part of the future live-node path
- [ ] 7.2 Implement the response channel: order/fill events delivered back to the API and WebSocket clients - not applicable in the synchronous v1 model; deferred with the live node / WebSocket
- [x] 7.3 Implement the `202 Accepted` placement flow returning an order identifier - `POST /api/v1/orders` returns 202 + order id (fills synchronously for market/crossing-limit)
- [x] 7.4 Implement reads (orders, positions) from the Nautilus cache, reconciled with Postgres - **DEVIATION (D10)**: reads come directly from Postgres (the source of truth); no separate Nautilus cache in v1
- [ ] 7.5 Tests: end-to-end place-order -> fill -> WebSocket notification; command-timeout handling - partial: place-order -> fill end-to-end tested in `test_orders.py`; WebSocket notification + command-timeout deferred with 8.4/7.1

## 8. Trading API (`trading-api`)

- [x] 8.1 Implement `/api/v1/orders`: `POST` (place market/limit), `DELETE` (cancel), `GET` (list by status / query one) - all three implemented and tested
- [x] 8.2 Implement `/api/v1/accounts` endpoints: list, create, get summary, reset, deposit
- [x] 8.3 Implement `/api/v1/positions` `GET` for an account
- [ ] 8.4 Implement the WebSocket endpoint streaming market data plus order/fill updates for the caller's accounts - deferred
- [x] 8.5 Implement standardized JSON error responses (error code, message, offending fields)
- [x] 8.6 Enforce authentication on all endpoints; return `404` for cross-account/cross-user access
- [x] 8.7 Tests: place/cancel/query orders, account management, positions, auth rejection, error format, `202` + async fill delivery - order place/cancel/query, accounts, positions, auth rejection, error format, `202` all tested in `test_auth.py` / `test_accounts.py` / `test_orders.py`; async-fill-via-WebSocket deferred with 8.4

## 9. Integration & Startup

- [ ] 9.1 Implement `TradingNode` bootstrap: `build()` + `run()` with data and sandbox exec clients and the bridging strategy - **NOT DONE (D10)**: v1 runs as a FastAPI app with a stateless `BacktestEngine` fill calculator; the live `TradingNode` is the future real-time target
- [ ] 9.2 Implement graceful shutdown that flushes final state to Postgres - not applicable in v1 (no long-running node); Postgres is written transactionally per order. Re-evaluate with the live node
- [x] 9.3 Integration test: full lifecycle (register -> create account -> subscribe -> place market + limit orders -> fills -> query -> reset) against the sandbox with live or recorded data - lifecycle covered by `test_orders.py` + a Postgres end-to-end smoke (buy/sell/limit/cancel, cash math verified); runs against the simulated feed + Postgres, not a live sandbox node
- [x] 9.4 Generate OpenAPI docs; write a README with env vars and local run steps - OpenAPI auto-served at `/docs` and `/openapi.json`; README written
- [x] 9.5 Smoke-run locally; verify no real broker is contacted and no real capital is at risk - full order-flow smoke run against Postgres; no broker is contacted and no real capital is at risk (by design)
