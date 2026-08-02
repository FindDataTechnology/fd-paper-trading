## Why

The v1 paper-trading system is functionally complete and green at the HTTP/integration level (58 backend tests, 4 frontend logic tests), but it carries real defects and gaps that undermine confidence and block safe evolution:

- A wrong config default: `Settings.market_data_provider` defaults to `"databento"`, which silently falls through to `SimulatedMarketDataProvider` because `create_app` only special-cases `"polygon"`. This contradicts `.env.example`, the README, and the `market-data` spec (which say the default is `simulated`).
- Stale docs: `CLAUDE.md` still says the repo is "greenfield, not yet bootstrapped"; `api/schemas.py`'s docstring says "only read endpoints are wired"; the `paper-trading-api` change is unarchived and its `tasks.md` marks the WebSocket (already implemented) as "deferred".
- A Nautilus `Pandas4Warning` (`Timestamp.utcnow` deprecated) surfaces on every test run from inside `BacktestEngine.run()` - noisy and not patchable upstream.
- Global-`Settings` coupling: `AccountService.create` and the `auth/security` token helpers call `get_settings()` (an `lru_cache` global) at call time, so the highest-risk logic - signed-quantity PnL, fill ledgering, JWT/API-token hashing - cannot be unit-tested without env mutation or cache clearing.
- The React frontend has unit tests for `lib/` and the realtime hook but **no component/render tests and no end-to-end test**; the backend has **no pure unit tests** for the service/execution layer.

This change hardens the project with a proper test pyramid and fixes the issues found along the way - without changing any API behavior.

## What Changes

- **Fix the config default**: `Settings.market_data_provider` default `"databento"` -> `"simulated"`, so code matches `.env.example`, the README, and the `market-data` spec. Observable behavior is unchanged (`"databento"` already fell through to simulated).
- **Decouple services from the global settings cache**: inject `Settings` into `AccountService` (threaded through the accounts routes via the app state) and into the auth token helpers, so service-layer logic is unit-testable with explicit inputs.
- **Extract a market-data provider factory**: `build_market_data_provider(settings) -> MarketDataProvider`, replacing the inline `if/elif/else` in `create_app`, used by both the app and tests.
- **Quiet the Nautilus deprecation warning** in the test run via `pytest` `filterwarnings` (the call is inside Nautilus internals).
- **Add a backend unit-test layer** (`tests/unit/`) covering pure logic with no HTTP/DB round-trip: `PaperExecutionService._update_position` PnL math (open, add, reduce, close, flip, short side), `NautilusFillEngine.compute_fill` (market fill, crossing limit, resting limit, rejection), `AccountService` create/deposit/withdraw/reset edge cases, JWT + API-token hashing/verification, and `ConnectionManager._wobble` quote math.
- **Strengthen backend e2e** (`tests/e2e/`): keep the existing full-flow test and add the limit-order lifecycle (rest -> cancel; rest -> crossing fill), account reset after trades, multi-account isolation, the API-token auth flow, and the bars/search contract - all against the simulated provider + in-memory SQLite via the ASGI transport.
- **Add React component tests** (Vitest + Testing Library): `OrderTicket` validation/submit, `OrdersList` cancel, `PositionsTable`, `SymbolSearch`, `AccountSwitcher`, `ProtectedRoute` redirect, and `Login`/`Register` error display.
- **Add a frontend e2e** (Playwright) driving the built SPA against the FastAPI backend through one full journey: register -> login -> create account -> search symbol -> place order -> see fill + position -> cancel order.
- **Bring docs in sync**: update `CLAUDE.md` (project is bootstrapped), fix the `api/schemas.py` docstring, refresh the README test section, and archive the done `paper-trading-api` change.
- **Wire CI** (`.github/workflows/ci.yml`) to run backend `pytest`, frontend `vitest`, and the Playwright e2e as separate jobs.

**Non-goals**: no REST API shape or response changes; no move to the live Nautilus `TradingNode` (still deferred); equity stays cash-only and account reset stays at-cost (documented limitations, unchanged here).

## Capabilities

### New Capabilities

- `test-suite`: The project's test-pyramid contract - backend unit tests for the service/execution/auth/marketdata logic, backend e2e for the full trading flow, React component tests, and a frontend e2e - plus the CI gates that enforce them and the warning-suppression policy.

### Modified Capabilities

None. This is a conservative refactor: every fix either aligns code with an existing spec (the config-default fix matches the `market-data` spec's "unset/unknown -> simulated" rule) or is internal/docs/test-only. No requirement-level behavior changes.

## Impact

- **Code**: `src/fd_trade/config.py`, `accounts/service.py`, `api/routes/accounts.py`, `api/app.py` (provider factory), `auth/security.py`, `auth/dependencies.py`. No API routes or response schemas change.
- **Tests**: new `tests/unit/` and `tests/e2e/` layout (existing HTTP tests reorganized but preserved); new `web/src/**/*.test.tsx` component tests and a `web/e2e/` Playwright suite.
- **CI**: `.github/workflows/ci.yml` gains frontend-test and e2e jobs.
- **Docs**: `CLAUDE.md`, `README.md`, `api/schemas.py` docstring; `openspec/changes/paper-trading-api` archived.
- **Dependencies**: add `@playwright/test` (dev) to `web/`; no backend dependency changes.
