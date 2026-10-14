## MODIFIED Requirements

### Requirement: Backend unit tests for service-layer logic

The system SHALL provide a `tests/unit/` suite of pure unit tests that exercise backend
logic without an HTTP round-trip or a real database where the logic permits. The suite
SHALL cover: signed-quantity position PnL via `PaperExecutionService._update_position`
(opening a long/short, adding to a position, reducing, fully closing, and flipping sides,
with correct `avg_price` and `realized_pnl`); the fill calculator's `compute_fill` (market
buy fills at ask, market sell fills at bid, crossing limit fills, non-crossing limit rests
with zero fill, unknown/empty result rejected); `AccountService` create (default and custom
starting balance, initial deposit transaction), deposit/withdraw (zero-amount and
over-withdrawal rejection), and reset (cancels open orders, flattens positions at cost,
restores the target balance); JWT issuance/decode and API-token
generation/hashing/`looks_like_api_token` dispatch; and `ConnectionManager._wobble`
(bid/ask straddle the mid, deterministic per tick count).

The suite SHALL also exercise the sandbox evaluator against a real subprocess, so that a
change to the sandbox worker's module path or invocation is caught by tests rather than
degrading silently.

#### Scenario: Position flip resets average price

- **WHEN** a long position of `10 @ 100` receives a `SELL` fill of `15` at `110`
- **THEN** the position quantity becomes `-5`, the average price resets to `110`, and
  `realized_pnl` increases by `(110 - 100) * 10`

#### Scenario: Fill calculator rests a non-crossing limit

- **WHEN** the fill calculator's `compute_fill` is called for a limit `BUY` whose price is
  below the ask
- **THEN** the result status is `OPEN` with `filled_quantity == 0` and an empty fills list

#### Scenario: Sandbox spawns a real subprocess

- **WHEN** the sandbox evaluator is exercised in a test
- **THEN** it spawns the worker as a real subprocess and returns its evaluation, proving the
  invocation path is intact

#### Scenario: Account service rejects over-withdrawal

- **WHEN** `AccountService.deposit` is called with a negative amount that would drive
  `cash_balance` below zero
- **THEN** it raises `ValueError` and leaves `cash_balance` unchanged

### Requirement: CI gates the test pyramid

The project's CI (`.github/workflows/ci.yml`) SHALL run three test jobs: backend
`uv run pytest`, frontend `npm run test` (Vitest), and the Playwright e2e. A failure in any
job SHALL fail the workflow. The backend test run SHALL NOT depend on a third-party trading
framework being installed.

#### Scenario: CI runs all test layers

- **WHEN** a pull request is opened
- **THEN** CI runs backend pytest, frontend vitest, and the Playwright e2e, and the workflow
  fails if any layer fails

#### Scenario: Nautilus warning is suppressed in the test run

- **WHEN** the backend test suite runs
- **THEN** the output contains no `Timestamp.utcnow is deprecated` warning — now trivially
  satisfied because the framework that emitted it is no longer installed, so no
  framework-specific warning filter is configured

#### Scenario: Backend tests run without a trading framework

- **WHEN** the backend test suite runs in a clean environment
- **THEN** it passes with no third-party trading framework installed
