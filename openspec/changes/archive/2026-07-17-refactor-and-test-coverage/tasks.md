## 1. Backend fixes & decoupling

- [x] 1.1 Fix `Settings.market_data_provider` default in `src/fd_trade/config.py` from `"databento"` to `"simulated"` (aligns code with `.env.example`, README, and the `market-data` spec; observable behavior unchanged).
- [x] 1.2 Extract `build_market_data_provider(settings) -> MarketDataProvider` into `src/fd_trade/marketdata/__init__.py` (maps `"polygon"` -> `PolygonMarketDataProvider`, everything else -> `SimulatedMarketDataProvider`); replace the inline `if/elif/else` in `create_app` with a call to it.
- [x] 1.3 Set `app.state.settings = get_settings()` in `create_app`; add an optional `settings: Settings | None = None` param to `AccountService.__init__` (default `get_settings()`); pass `app.state.settings` into `AccountService` from the accounts routes.
- [x] 1.4 Add an optional `settings: Settings | None = None` argument to `create_access_token` and `decode_access_token` in `auth/security.py` (default `get_settings()`); keep `auth/dependencies.py` working unchanged.
- [x] 1.5 Add a targeted `filterwarnings` rule to `[tool.pytest.ini_options]` in `pyproject.toml` that ignores the exact Nautilus `Timestamp.utcnow is deprecated` `Pandas4Warning` (match message + module, not a broad pattern).
- [x] 1.6 Run `uv run pytest`; confirm the existing 58 tests still pass and the Nautilus warning no longer appears in the summary.

## 2. Backend unit tests (`tests/unit/`)

- [x] 2.1 `tests/unit/test_position_math.py` - assert `PaperExecutionService._update_position` directly: open long, open short, add same-direction (weighted avg, no PnL), reduce (realize PnL, avg unchanged), fully close (quantity 0), flip long->short and short->long (avg resets to fill price, realized PnL on the closing leg).
- [x] 2.2 `tests/unit/test_fill_engine.py` - assert `NautilusFillEngine.compute_fill` directly: market buy fills at ask, market sell fills at bid, crossing limit fills immediately, non-crossing limit returns status `OPEN` with zero fill, empty/None order result returns `REJECTED`.
- [x] 2.3 `tests/unit/test_account_service.py` - assert `AccountService` against an in-memory SQLite session: create with default and custom balance (and an initial `deposit` transaction), deposit/withdraw (zero-amount and over-withdrawal raise), reset after a trade (open orders cancelled, positions flattened at cost, balance restored to target).
- [x] 2.4 `tests/unit/test_auth_security.py` - assert `hash_password`/`verify_password`, JWT issue->decode round-trip, expired/tampered token raises `TokenError`, and `generate_api_token`/`hash_api_token`/`looks_like_api_token` (tokens start with `fdt_`, hash is `sha256$...`, plaintext != hash). Pass `settings` explicitly to exercise the injected path.
- [x] 2.5 `tests/unit/test_realtime_wobble.py` - assert `ConnectionManager._wobble`: bid < mid < ask, bid/ask straddle the quote mid, and the output is deterministic for a given tick count + symbol.
- [x] 2.6 Run `uv run pytest tests/unit/`; confirm all unit tests pass with no HTTP round-trip and no external services.

## 3. Backend end-to-end tests (`tests/e2e/`)

- [x] 3.1 Create `tests/e2e/` and move `tests/test_e2e_flow.py` -> `tests/e2e/test_full_trading_flow.py` (update imports; keep the existing fixtures from `conftest.py`).
- [x] 3.2 `tests/e2e/test_limit_outcomes.py` - assert the real v1 limit behavior: a non-crossing limit rests as `OPEN` (no position, appears under `?status=OPEN`) and is then cancellable; a crossing limit fills immediately at placement. Do NOT assert that a resting limit later fills on its own (stateless engine).
- [x] 3.3 `tests/e2e/test_account_reset.py` - after opening a position and resting an order, `POST /accounts/{id}/reset` cancels the open order, flattens the position at cost, and restores the target balance; verify via the summary and positions/orders lists.
- [x] 3.4 `tests/e2e/test_isolation.py` - end-to-end multi-account (one user, two accounts, independent positions/cash) and cross-user denial (404 `not_found`) for account reads, deposits, and order placement.
- [x] 3.5 `tests/e2e/test_api_token_flow.py` - create an API token via `POST /api/v1/tokens`, use it as `Authorization: Bearer fdt_...` to place an order (success), revoke it via `DELETE /api/v1/tokens/{id}`, then retry a protected request (401).
- [x] 3.6 `tests/e2e/test_bars_search.py` - search filtering + empty/no-match, bars for each supported timeframe, oldest-first ordering with correct step, `limit` clamping, and unknown timeframe returns 400 `invalid_timeframe`.
- [x] 3.7 Run `uv run pytest tests/e2e/`; confirm all e2e tests pass against the simulated provider + in-memory SQLite.

## 4. Frontend component tests (Vitest + Testing Library)

- [x] 4.1 Verify `web/vite.config.ts` enables the jsdom environment for `*.test.tsx` (add a `test.environment` override if needed); confirm `@testing-library/react` and `@testing-library/jest-dom` resolve.
- [x] 4.2 `web/src/components/OrderTicket.test.tsx` - invalid input (empty/non-positive quantity, LIMIT without price) prevents submission and makes no API call; valid input calls the mocked api client with the right payload.
- [x] 4.3 `web/src/components/OrdersList.test.tsx` - renders a list of orders and invokes the cancel callback (mocked api) on cancel click.
- [x] 4.4 `web/src/components/PositionsTable.test.tsx` - renders open positions with quantity and average price.
- [x] 4.5 `web/src/components/SymbolSearch.test.tsx` - typeahead filters results from the mocked api and selects a symbol.
- [x] 4.6 `web/src/components/AccountSwitcher.test.tsx` - lists accounts and switches the active account.
- [x] 4.7 `web/src/components/ProtectedRoute.test.tsx` - redirects to `/login` when no token is stored; renders children when authenticated.
- [x] 4.8 `web/src/pages/Login.test.tsx` and `Register.test.tsx` - display the API error message on failure and do not navigate away.
- [x] 4.9 Run `cd web && npm run test`; confirm all component tests pass.

## 5. Frontend end-to-end test (Playwright)

- [x] 5.1 Add `@playwright/test` to `web/package.json` devDependencies; add `web/playwright.config.ts` with a `webServer` (`web/e2e/run-backend.sh`) that builds the SPA if needed and starts the backend (`DATABASE_URL=sqlite+aiosqlite:///./e2e.db`, `MARKET_DATA_PROVIDER=simulated`, schema initialized via `init_db` after importing the models) on a dedicated e2e port (8001) so it never reuses a developer's :8000 server.
- [x] 5.2 `web/e2e/happy-path.spec.ts` - register a new user -> log in -> create a paper account -> search a symbol -> place a market order -> observe the fill and the resulting position in the UI -> place a resting limit -> cancel it and see it leave the Open list.
- [x] 5.3 Run `cd web && npx playwright test`; confirm the happy path passes headless.
- [x] 5.4 (Found during e2e) Fix `PolygonMarketDataProvider` - it inherited `raise NotImplementedError` for `search_instruments`/`get_bars`, so `/instruments/search` and `/instruments/{symbol}/bars` returned HTTP 500 under `MARKET_DATA_PROVIDER=polygon` (the local default). Delegate both to a `SimulatedMarketDataProvider` fallback (Polygon free tier has no search/bars endpoints); added `tests/unit/test_polygon_provider.py` regression tests.

## 6. CI

- [x] 6.1 Ensure `.github/workflows/ci.yml` has a backend job running `uv sync` + `uv run pytest` (with the new `filterwarnings`).
- [x] 6.2 Add a frontend job running `cd web && npm ci && npm run test` (Vitest).
- [x] 6.3 Add a Playwright e2e job: install backend + frontend deps, build the SPA, start the backend against SQLite with schema initialized, `npx playwright install --with-deps`, run `npx playwright test`; cache `~/.cache/ms-playwright`.
- [x] 6.4 Validate the workflow YAML (`actionlint` or `gh workflow view`); confirm the three jobs are wired to fail the workflow on any failure.

## 7. Docs & cleanup

- [x] 7.1 Rewrite the "Project status" section of `CLAUDE.md` to reflect reality: the project is bootstrapped (FastAPI + Nautilus paper-trading API + React SPA), the v1 execution model is a stateless `BacktestEngine` fill calculator, and the live `TradingNode` is deferred. Remove the "greenfield / not yet bootstrapped" language.
- [x] 7.2 Fix the stale docstring in `src/fd_trade/api/schemas.py` (order placement/cancellation endpoints ARE wired, not just reads).
- [x] 7.3 Update the README "Tests" section to document the `tests/unit/`, `tests/e2e/` layout, the frontend component tests, the Playwright e2e, and the CI gates.
- [x] 7.4 Archive the `paper-trading-api` change (`openspec archive paper-trading-api --skip-specs`) after confirming its `tasks.md` reflects what shipped; capture any genuinely-still-open items as notes.
- [x] 7.5 Final verification: `uv run pytest` (all backend layers), `cd web && npm run test`, and `npx playwright test` all green; `openspec validate refactor-and-test-coverage --strict` passes.
