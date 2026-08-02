## Context

The fd-trade v1 paper-trading system is a FastAPI + Nautilus-Trader backend with a React/Vite SPA. It is functionally complete: 58 backend tests and 4 frontend logic tests pass. The backend tests, however, are almost all HTTP-level integration tests driven through httpx's ASGI transport; the highest-risk logic - signed-quantity position PnL (`PaperExecutionService._update_position`), the Nautilus fill calculator, account ledgering, and token hashing - has **no isolated unit tests**. Several services also read configuration through a process-global `get_settings()` `lru_cache` at call time, which makes that logic impossible to unit-test without env mutation or cache clearing.

Alongside the test gap there are concrete defects (see `proposal.md`): a misleading `market_data_provider` default (`"databento"`), stale docs, a noisy Nautilus `Pandas4Warning`, and an unarchived stale change. The React frontend has unit tests for `lib/` and the realtime hook but no component or e2e tests.

Constraints: this is a **conservative** refactor. No REST API shape or response schema may change, no DB migration, no move to the live `TradingNode`. Equity stays cash-only and reset stays at-cost. The work must keep the existing 58+4 tests green throughout.

## Goals / Non-Goals

**Goals:**
- Make the service/execution/auth/marketdata logic unit-testable by removing global-`Settings` coupling and centralizing provider construction.
- Establish a test pyramid: pure unit tests (fast, no I/O) at the base, HTTP-level e2e in the middle, and a real-browser frontend e2e at the top.
- Fix the config default, stale docs, and the Nautilus warning; archive the stale `paper-trading-api` change.
- Gate all three test layers in CI.

**Non-Goals:**
- Changing any API route, status code, or response shape.
- Marking positions to market (equity remains cash-only) or flattening at market on reset (remains at-cost).
- Building the live Nautilus `TradingNode` / async command-queue bridge (still deferred per the v1 design, decision D10).
- Rewriting the frontend component architecture.

## Decisions

### D1: Inject `Settings` via constructor with a `get_settings()` fallback
`AccountService` and the auth token helpers currently call `get_settings()` inline. We add an optional `settings: Settings | None = None` constructor/argument parameter that defaults to `get_settings()` when omitted.

- **Why over the alternative** (clearing the `lru_cache` in tests with `get_settings.cache_clear()`): explicit injection is stable, thread-safe, and reads as a dependency rather than a test hack. The default preserves the convenience for existing callers (routes, `create_app`) so no callsite is forced to change to keep working.
- **Why over** always-required injection: it would force every route and `create_app` to thread settings through immediately, churning more code than a conservative change warrants. The default keeps the blast radius small while still enabling pure unit tests (which pass `settings` explicitly).

### D2: A single `build_market_data_provider(settings) -> MarketDataProvider` factory
The `if settings.market_data_provider == "polygon": ... else: SimulatedMarketDataProvider()` block lives inline in `create_app`. We extract it into `marketdata/__init__.py` (or `provider.py`) as `build_market_data_provider`, called by `create_app` and reusable by tests.

- **Why**: removes duplication, gives the selection logic one home, and makes the "default -> simulated" rule explicit and unit-testable (the factory, not the app, owns the fallthrough). The current `"databento"`-falls-through-to-simulated behavior becomes a deliberate `"simulated"` default in `Settings` plus a factory that maps unknown values to simulated.

### D3: Test layout - add subdirs, do not move existing tests
New tests live in `tests/unit/` (pure-logic, no HTTP/DB where possible) and `tests/e2e/` (full ASGI flows). Existing `tests/test_*.py` files stay at the `tests/` root.

- **Why over** a full reorg into `tests/integration/`: moving 8 passing test files churns git history and risks breaking cached nodeids / IDE mappings for no behavioral gain. `testpaths = ["tests"]` already collects recursively, so subdirs are discovered for free. The pyramid is expressed by directory convention, not by a disruptive move.

### D4: Suppress the Nautilus `Pandas4Warning` with a targeted `filterwarnings` rule
`BacktestEngine.run()` calls `pandas.Timestamp.utcnow()` internally, emitting `Pandas4Warning: Timestamp.utcnow is deprecated`. We add a `filterwarnings` entry in `pyproject.toml` `[tool.pytest.ini_options]` that ignores that **specific message** only.

- **Why over** pinning pandas / patching Nautilus: the call is upstream and not patchable without monkeypatching private Nautilus internals. Pinning pandas below the deprecation is a regression trap. A targeted `ignore::DeprecationWarning` matching the exact message keeps the test output clean while still surfacing any *other* deprecation.
- **Why over** `filterwarnings = ["error"]` (treat all warnings as errors): would turn the Nautilus warning into a hard failure with no upstream fix available. We keep warnings visible-but-clean instead.

### D5: Frontend e2e with Playwright; component tests with Vitest + Testing Library
The frontend already uses Vitest; we keep it for component tests and add `@playwright/test` for a single happy-path e2e that builds the SPA and drives it against the real FastAPI backend.

- **Why Playwright over Cypress**: Playwright has first-class Vite awareness, a single binary install, and a fast headless driver; Cypress is heavier and its Vite story is weaker. One happy-path journey keeps the added CI cost bounded.
- **Component-test network strategy**: mock the `web/src/api/client` module (axios instance) per test rather than spinning up MSW. The existing `lib/` tests already follow this no-network pattern; component tests extend it.

### D6: `app.state.settings` for route-level access
Routes that need settings (e.g. the accounts routes feeding `AccountService`) read `request.app.state.settings`, set once in `create_app`. This pairs with D1: the route passes `app.state.settings` into `AccountService(settings=...)`.

- **Why**: avoids each route calling `get_settings()` and gives tests one knob (`app.state.settings`) to override. `Settings` is cheap to construct and immutable in practice.

### D7: No migration, no schema change
The config-default fix changes a Python default, not a column. No Alembic revision is needed.

## Risks / Trade-offs

- **[Playwright e2e is slow and can be flaky in CI]** -> Mitigation: one happy-path journey only; `retries: 1` in CI; cache `~/.cache/ms-playwright` browser binaries; the job is non-blocking for the backend/vitest jobs (runs separately).
- **[Optional `settings` injection leaves the global-coupling default in place]** -> Mitigation: accepted trade-off for a conservative change; pure unit tests always pass `settings` explicitly, so the tested path is fully decoupled. The default is a convenience for prod routes, not a correctness dependency.
- **[`filterwarnings` could hide a future real warning with the same message]** -> Mitigation: match on the exact `Timestamp.utcnow is deprecated` string and module, not a broad pattern; reviewed when nautilus_trader is upgraded.
- **[Settings default change could surprise a deploy that relied on `databento`]** -> Mitigation: `databento` was never implemented (it fell through to simulated), so no deploy could have been relying on real Databento data. The change is observable only as "the config file now says what it does." Documented in the README and the changelog.
- **[Adding component tests may expose existing UI bugs]** -> Mitigation: that is a feature, not a risk - any bug surfaced becomes a task. Tests assert current behavior; if current behavior is wrong, the task list captures the fix rather than silently encoding the bug.
