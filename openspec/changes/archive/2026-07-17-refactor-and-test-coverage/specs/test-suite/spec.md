## ADDED Requirements

### Requirement: Backend unit tests for service-layer logic

The system SHALL provide a `tests/unit/` suite of pure unit tests that exercise backend logic without an HTTP round-trip or a real database where the logic permits. The suite SHALL cover: signed-quantity position PnL via `PaperExecutionService._update_position` (opening a long/short, adding to a position, reducing, fully closing, and flipping sides, with correct `avg_price` and `realized_pnl`); `NautilusFillEngine.compute_fill` (market buy fills at ask, market sell fills at bid, crossing limit fills, non-crossing limit rests with zero fill, unknown/empty result rejected); `AccountService` create (default and custom starting balance, initial deposit transaction), deposit/withdraw (zero-amount and over-withdrawal rejection), and reset (cancels open orders, flattens positions at cost, restores the target balance); JWT issuance/decode and API-token generation/hashing/`looks_like_api_token` dispatch; and `ConnectionManager._wobble` (bid/ask straddle the mid, deterministic per tick count).

#### Scenario: Position flip resets average price
- **WHEN** a long position of `10 @ 100` receives a `SELL` fill of `15` at `110`
- **THEN** the position quantity becomes `-5`, the average price resets to `110`, and `realized_pnl` increases by `(110 - 100) * 10`

#### Scenario: Fill calculator rests a non-crossing limit
- **WHEN** `NautilusFillEngine.compute_fill` is called for a limit `BUY` whose price is below the ask
- **THEN** the result status is `OPEN` with `filled_quantity == 0` and an empty fills list

#### Scenario: Account service rejects over-withdrawal
- **WHEN** `AccountService.deposit` is called with a negative amount that would drive `cash_balance` below zero
- **THEN** it raises `ValueError` and leaves `cash_balance` unchanged

### Requirement: Backend end-to-end tests via the ASGI transport

The system SHALL provide a `tests/e2e/` suite that drives the full FastAPI application through httpx's ASGI transport against the simulated market-data provider and an in-memory SQLite database, requiring no external services. The suite SHALL cover the complete user journey (register -> login -> create account -> search symbol -> fetch bars -> open WebSocket and subscribe -> place order -> receive order/fill event over `/ws` -> position appears), the limit-order placement outcomes (a non-crossing limit rests as `OPEN` and is then cancellable; a crossing limit fills immediately at placement), account reset after trades, multi-account and cross-user isolation, the API-token authentication flow (create token, use it in place of a JWT, revoke it), and the bars/search contract (supported timeframes, oldest-first ordering, unknown timeframe rejected). Because v1 computes fills statelessly per order, the suite SHALL NOT assert that a resting limit later fills on its own.

#### Scenario: Full trading flow over HTTP and WebSocket
- **WHEN** a client registers, logs in, creates an account, subscribes to a symbol over `/ws`, and places a market `BUY`
- **THEN** the order returns `202` with status `FILLED`, an `order` event arrives over `/ws`, and a position for the symbol appears in the account

#### Scenario: Revoked API token is rejected
- **WHEN** a user creates an API token, uses it successfully, then revokes it and retries a protected request
- **THEN** the post-revoke request returns `401`

### Requirement: Frontend component tests

The system SHALL provide Vitest + React Testing Library component tests under `web/src/**/__tests__/` (or colocated `*.test.tsx`) that render components with the API client mocked and assert behavior without a network. The suite SHALL cover `OrderTicket` (validates inputs and submits a well-formed order), `OrdersList` (renders orders and invokes cancel), `PositionsTable`, `SymbolSearch` (typeahead filtering), `AccountSwitcher`, `ProtectedRoute` (redirects unauthenticated users to `/login`), and `Login`/`Register` (display API errors and do not navigate on failure).

#### Scenario: Order ticket rejects invalid input
- **WHEN** the `OrderTicket` is rendered with an empty quantity and submit is pressed
- **THEN** submission is prevented and no API call is made

#### Scenario: Protected route redirects when unauthenticated
- **WHEN** `ProtectedRoute` renders with no stored token
- **THEN** it redirects to `/login`

### Requirement: Frontend end-to-end test

The system SHALL provide a Playwright end-to-end test under `web/e2e/` that builds the SPA and drives a real browser against the running FastAPI backend through one full journey: register a new user -> log in -> create a paper account -> search a symbol -> place a market order -> observe the fill and resulting position in the UI -> cancel an order. The test SHALL run headless and require no manual setup beyond starting the backend and building the SPA.

#### Scenario: Happy-path trading journey in a browser
- **WHEN** the Playwright suite runs against the built SPA and backend
- **THEN** a user can register, log in, create an account, place an order, see the position appear, and cancel an order, all through the real UI

### Requirement: CI gates the test pyramid

The project's CI (`.github/workflows/ci.yml`) SHALL run three test jobs: backend `uv run pytest`, frontend `npm run test` (Vitest), and the Playwright e2e. A failure in any job SHALL fail the workflow. The backend pytest run SHALL NOT emit the Nautilus `Timestamp.utcnow is deprecated` warning.

#### Scenario: CI runs all test layers
- **WHEN** a pull request is opened
- **THEN** CI runs backend pytest, frontend vitest, and the Playwright e2e, and the workflow fails if any layer fails

#### Scenario: Nautilus warning is suppressed in the test run
- **WHEN** the backend test suite runs
- **THEN** the output contains no `Timestamp.utcnow is deprecated` warning
