## Why

The paper-trading system described by the `paper-trading-api` change is **already implemented and tested** (31 passing tests, smoke-tested against real Postgres), but its contract was never consolidated into `openspec/specs/` - that directory is still empty. Without a spec baseline, every future change (real-time streaming, live `TradingNode`, the web UI's backend additions) has no formal contract to delta against. This change formalizes the **as-built** system as the canonical capability specs, so subsequent changes modify requirements rather than re-deriving them from code each time.

## What Changes

- Introduce the canonical spec for the **as-built multi-tenant paper-trading service** (`paper-trading-api` baseline): five capabilities covering auth, accounts, market data, order execution, and the REST API - matching the running `src/fd_trade/` implementation.
- Document the **as-built execution model**: Nautilus `BacktestEngine` used as a **stateless per-order fill calculator** (decision D10) against a market-data provider quote, with PostgreSQL as the account ledger and source of truth - not the live `TradingNode` originally proposed.
- Document the **as-built market-data model**: a `MarketDataProvider` interface with a `Simulated` implementation (deterministic, for tests) and a `Polygon` implementation (free-tier previous-close aggregate, end-of-day prices with a synthesized spread) - real-time intraday NBBO is deferred.
- **Scope boundary**: this change formalizes the `paper-trading-api` baseline only. The as-built repo also contains a real-time WebSocket layer (`/ws` + `src/fd_trade/realtime/`), instrument search/bars endpoints (`/api/v1/instruments/*`), and a `web/` SPA - these are built but owned by the in-flight `trading-web-ui` change and are out of scope here.
- No code changes. This is a documentation/spec-only change that captures behavior the code already exhibits. Nothing in `src/fd_trade/` is added, removed, or modified.
- **BREAKING**: None. No API or behavior changes; the spec describes the existing, already-shipped `/api/v1` contract verbatim.

## Capabilities

### New Capabilities
- `user-auth`: Multi-user registration and authentication - bcrypt password hashing, short-lived JWT access tokens, long-lived revocable API tokens (`fdt_`-prefixed), and a bearer-token dependency that accepts either credential type and resolves the requesting user.
- `paper-accounts`: Multi-account paper trading owned by users - account creation with a starting cash balance, list/summary, signed deposit/withdrawal, reset (cancel open orders, flatten positions at cost basis, restore starting balance), and strict per-user/per-account isolation.
- `market-data`: A `MarketDataProvider` interface returning a top-of-book `Quote` and a Nautilus `Instrument` per symbol, with `Simulated` (deterministic) and `Polygon` (real previous-close) implementations; `get_quote` returns `None` on a data gap, and unknown symbols yield no instrument.
- `order-execution`: Simulated order matching via Nautilus's `OrderMatchingEngine` (stateless `BacktestEngine` fill calculator) - market and limit orders, fills applied to a signed-quantity position ledger and cash ledger in PostgreSQL, with buying-power checks, cancellation, and per-account isolation by `account_id`.
- `trading-api`: FastAPI REST API under `/api/v1` exposing auth, accounts, positions, and order placement/cancellation/query; standardized JSON error responses; `202 Accepted` on order placement; all resources scoped to the authenticated user with `404` on cross-user/cross-account access.

### Modified Capabilities
<!-- None. openspec/specs/ is empty, so every capability above is being introduced for the first time. The implemented /api/v1 REST contract is captured as-is. -->

## Impact

- **Specs only**: populates `openspec/specs/{user-auth,paper-accounts,market-data,order-execution,trading-api}/spec.md` from the as-built behavior in `src/fd_trade/`.
- **No code impact**: `src/fd_trade/` (auth, accounts, marketdata, execution, persistence, api) is unchanged. This change is the formal record of what that code does today.
- **No dependency/infra impact**: the stack (FastAPI, SQLAlchemy 2.0 async + asyncpg/aiosqlite, Alembic, Nautilus Trader, python-jose, passlib/bcrypt, httpx) and Postgres-on-5433 dev setup are already in place.
- **Downstream effect**: future changes (`trading-web-ui` for the realtime/instruments/web layers, a future live `TradingNode` change, AI-signal ingestion) now delta against these specs instead of describing the baseline inline.
