## Context

The paper-trading system is **already implemented** in `src/fd_trade/` (auth, accounts, marketdata, execution, persistence, api) and is the running, tested foundation of fd-trade. This change introduces **no new code** - it formalizes that as-built system as canonical capability specs so future changes (`trading-web-ui`, the deferred real-time `TradingNode` + WebSocket layer, AI-signal ingestion) delta against a spec contract instead of re-reading source.

The system is a multi-tenant stock paper-trading service: many users, each owning multiple paper accounts, placing market/limit stock orders through a versioned REST API, filled by a simulated engine against market-data quotes - no broker, no real capital. It is built on Nautilus Trader (per `CLAUDE.md`) but, as implemented, uses Nautilus only as a fill calculator, with PostgreSQL as the ledger.

Key as-built constraints discovered during the original `paper-trading-api` implementation:
- Nautilus's global Rust logger initializes **once per process**, so only one `BacktestEngine`/`TradingNode` can exist reliably alongside FastAPI. This drove the stateless-calculator approach.
- Per-account hyphenated venue names break the backtest account issuer, and dynamic per-account venue registration on a *running* node is non-trivial - so account isolation is done in Postgres, not in Nautilus venues.
- The Polygon **free tier** is not entitled to real-time NBBO/last-trade (HTTP 403); only the previous-close aggregate is available, so real prices are end-of-day, not intraday.

**Scope of this change:** this change formalizes the `paper-trading-api` baseline - five capabilities (`user-auth`, `paper-accounts`, `market-data`, `order-execution`, `trading-api`) covering the REST API, multi-account/multi-user isolation, simulated execution, and the market-data provider. The as-built repo *also* contains a real-time WebSocket layer (`/ws` + `src/fd_trade/realtime/`), instrument search/bars endpoints (`/api/v1/instruments/*`), and a `web/` SPA. These are **built but owned by the in-flight `trading-web-ui` change** (17/59 tasks done, not archived) and are intentionally out of scope here to avoid overlapping two changes. This design therefore describes only the `paper-trading-api` baseline; the realtime/instruments/web layers are formalized by `trading-web-ui`.

## Goals / Non-Goals

**Goals:**
- Record the as-built architecture and the key technical decisions (especially the D10 stateless-calculator deviation from the originally-proposed live `TradingNode`) as the formal design baseline.
- Document how multi-user / multi-account isolation, simulated execution, market data, and the REST API actually work today.
- Give future changes a stable design reference to extend or supersede.

**Non-Goals:**
- Introduce new code, dependencies, or behavior. This is a documentation-only change.
- The live `TradingNode` + `SandboxLiveExecClientFactory`, the async command-queue bridge, and real-time market data (Databento intraday NBBO) - these remain **deferred** future work (the D10 stateless-calculator model still holds).
- Real-time WebSocket streaming (`/ws`), the instrument search/bars endpoints (`/api/v1/instruments/*`), and the `web/` SPA are **built in code** but are owned by the in-flight `trading-web-ui` change and formalized there - out of scope for this baseline (see Context).
- The AI quant subsystem.

## Decisions

### D1. PostgreSQL is the source of truth; Nautilus is a stateless fill calculator
Users, accounts, orders, positions, fills, and balance transactions live in PostgreSQL (SQLAlchemy 2.0 async + asyncpg; aiosqlite for tests). Every order and fill is written transactionally. Nautilus's `OrderMatchingEngine` (via a `BacktestEngine` constructed per order with `bypass_logging=True`) is used **only** to compute how an order fills against a quote (fill price, partial vs full, rest vs reject). Account balances and positions are never held in Nautilus. **Why over the originally-proposed live `TradingNode` + sandbox exec client**: the one-logger-per-process constraint and the dynamic-per-account-venue lifecycle made the live-node model impractical to run reliably inside FastAPI; the stateless calculator uses the *same* Nautilus matching engine for correct market/limit semantics, is fully testable without a live feed, and delivers the core ask. **Alternative**: a hand-rolled matching engine (rejected - reinvents Nautilus's fill model and diverges from the project's stated foundation).

### D2. Account isolation by `account_id` in Postgres, not per-account Nautilus venues
Each account has a `PAPER-<12-hex>` venue *string* stored for identification, but the simulated matching runs on a shared `SIM` venue. Orders are computed independently per account, so they cannot cross-match; positions and cash are partitioned by `account_id` (and `user_id`). All repository lookups filter by `user_id` (and account ownership), returning `404` for other users' resources. **Why**: avoids the per-account-venue registration lifecycle problem on a running node while keeping isolation airtight at the data layer. **Alternative**: per-account simulated venues in one `TradingNode` (the originally-proposed D3; deferred with the live node).

### D3. Market data behind a swappable `MarketDataProvider` interface
A single interface (`get_quote`/`get_instrument`) is implemented by `SimulatedMarketDataProvider` (deterministic, for tests/dev) and `PolygonMarketDataProvider` (real previous-close with a synthesized spread, cached per symbol). Provider selection is via `MARKET_DATA_PROVIDER`. `get_quote` returns `None` on a data gap, which the execution layer turns into a `no_quote` rejection. **Why**: keeps the execution layer provider-agnostic and lets a real-time provider (Databento NBBO) replace the Polygon previous-close later without touching execution or the API.

### D4. Auth: JWT access tokens + revocable API tokens, bcrypt
Short-lived JWTs (HS256, bounded expiry) for interactive sessions; long-lived, revocable `fdt_`-prefixed API tokens (SHA-256 hashed at rest, plaintext shown once) for programmatic order placement - the user's "make an order through the API" use case. A single bearer dependency accepts either credential type. **Why**: API tokens are the natural fit for automated trading clients and are easy to rotate/revoke independently of JWTs.

### D5. API surface: FastAPI REST under `/api/v1`, standardized errors, `202` on order placement
REST for auth, accounts, positions, and orders; standardized `{"error": {code, message, fields}}` envelope (with a status-to-code map and validation `fields`); `POST /orders` returns `202 Accepted` because placement is the contractually acknowledged action (fills happen synchronously today but the `202` leaves room for the future async command-queue bridge). **Why**: FastAPI gives async + Pydantic validation + auto OpenAPI; versioning keeps future changes non-breaking.

### D6. Execution runs off the event loop; transaction boundary in the route
The Nautilus fill calculation is synchronous and ~10-20ms, so it is offloaded via `asyncio.to_thread` to avoid blocking the FastAPI loop. The route owns the DB transaction boundary (commit after the service mutates); repositories `flush` but do not commit. **Why**: keeps the API responsive while preserving a single atomic write per order. **Alternative**: the originally-proposed async command-queue bridge to a separate Nautilus loop (deferred with the live node).

## Risks / Trade-offs

- **[Fills are not real-time]** Fills use end-of-day/synthesized quotes, not live intraday NBBO. (The `/ws` streaming layer exists, but its ticks wobble the same static quote - it is not a real-time market-data feed.) -> Mitigation: accepted for paper trading v1; the provider interface lets a real-time feed slot in without API or execution changes (D3). Documented in API responses / README.
- **[Not broker-realistic fills]** No rejections or partials from liquidity constraints beyond the matching engine's model; sells always fill (the calculator runs in `MARGIN` mode with a huge notional balance). -> Mitigation: accepted trade-off for paper trading; the Postgres ledger decides whether a sell closes a long or opens a short, so account semantics stay correct.
- **[Equity is cash-only]** `equity == cash_balance` until positions are marked to market. -> Mitigation: documented in the account summary; marking to market arrives with the real-time market-data layer.
- **[Reset flattens at cost basis]** Account reset returns each position's cost to cash (zero realized PnL), not market value. -> Mitigation: accepted until live prices are available; noted in the service docstring.
- **[Spec-vs-code scope gap, intentional]** These specs cover only the `paper-trading-api` baseline; the built realtime/instruments/web layers are deliberately not spec'd here (owned by `trading-web-ui`). -> Mitigation: documented explicitly in Non-Goals; `trading-web-ui` formalizes those capabilities when it completes.
- **[Spec-vs-code drift]** For the in-scope baseline, these specs describe as-built behavior; if the code later diverges, the specs can lag. -> Mitigation: future changes must delta these specs, keeping them the living contract.
- **[Single process blast radius]** One FastAPI process serves all accounts; a crash affects everyone. -> Mitigation: Postgres is durable, so recovery is a restart; the design permits sharding later without API changes.

## Migration Plan

No migration - this is a spec-only change. No data, API, or behavior changes. Rollout is: merge the spec files into `openspec/changes/paper-trading-system/`, then (on `/opsx:apply` or archive) consolidate them into `openspec/specs/`. Rollback is removing the change directory; no production impact.

## Open Questions

- **When to supersede D1/D2/D6 with the live `TradingNode`**: a future change will revisit the one-logger-per-process constraint (e.g. a dedicated node process) and dynamic per-account venue registration. Out of scope here. Note: real-time WebSocket *streaming* is already built (see `trading-web-ui`); what remains deferred is the live execution *node* and real-time market *data*.
- **Mark-to-market for equity**: arrives with real-time market data; the `equity` field is already present and reserved for it.
