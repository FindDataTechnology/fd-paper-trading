# order-execution Specification

## Purpose
TBD - created by archiving change paper-trading-system. Update Purpose after archive.
## Requirements
### Requirement: Stateless Nautilus fill calculator

The system SHALL compute order fills using Nautilus's `OrderMatchingEngine` via a stateless `BacktestEngine`-based fill calculator (one engine per order, `bypass_logging=True`, on a `SIM` venue with a generous starting balance). Account state (positions, balances) SHALL NOT live in the Nautilus engine; it SHALL live in PostgreSQL, so accounts are isolated by `account_id` without per-account Nautilus venues. The calculator SHALL run off the FastAPI event loop (offloaded) so the API is not blocked.

#### Scenario: Calculator computes a fill price
- **WHEN** a market buy for a symbol with a current quote is placed
- **THEN** the calculator returns the fill price (the ask) without touching account balances

### Requirement: Market order fills

A market order SHALL fill immediately against the current quote: a buy fills at the `ask`, a sell fills at the `bid`. The resulting order status SHALL be `FILLED` when the full quantity is filled. Each fill SHALL be recorded as an immutable `Fill` row.

#### Scenario: Market buy fills at ask
- **WHEN** a user places a market `BUY` of `10` shares
- **THEN** the order status is `FILLED`, `filled_quantity` is `10`, `avg_fill_price > 0`, and exactly one fill is recorded

#### Scenario: Market sell after a buy reduces the position
- **WHEN** a user holds `10` long and places a market `SELL` of `4`
- **THEN** the position quantity becomes `6`

### Requirement: Limit order fills and resting

A limit order whose price crosses the quote SHALL fill immediately. A limit buy SHALL fill if its price is at or above the `ask`; otherwise it SHALL rest with status `OPEN` and `filled_quantity=0` (no position created, recorded as an open order). Limit orders require a `limit_price`.

#### Scenario: Crossing limit fills immediately
- **WHEN** a user places a limit `BUY` with a price far above the ask
- **THEN** the order status is `FILLED`

#### Scenario: Non-crossing limit rests
- **WHEN** a user places a limit `BUY` at a price below the ask
- **THEN** the order status is `OPEN`, `filled_quantity` is `0`, no position is created, and the order appears under `?status=OPEN`

### Requirement: Buying-power check

For a `BUY`, the system SHALL reject the order with `400` code `insufficient_buying_power` when the account's `cash_balance` is less than `quantity * ref_price`, where `ref_price` is the `ask` for market and crossing-limit buys, and the `limit_price` for resting limit buys. Sells are not subject to a cash buying-power check.

#### Scenario: Buy exceeding cash rejected
- **WHEN** a user with `1000` cash places a market `BUY` of `100000` shares
- **THEN** the system responds `400` with `{"error": {"code": "insufficient_buying_power", ...}}`

### Requirement: Unknown instrument and missing quote rejection

If the provider returns no instrument for a symbol, the system SHALL reject placement with `400` code `unknown_instrument`. If the provider returns no quote (data gap), the system SHALL reject placement with `400` code `no_quote`.

#### Scenario: Unknown instrument
- **WHEN** a user places an order for a symbol the provider does not know
- **THEN** the system responds `400` with `{"error": {"code": "unknown_instrument", ...}}`

#### Scenario: No quote available
- **WHEN** a user places a market order for a symbol with no current quote
- **THEN** the system responds `400` with `{"error": {"code": "no_quote", ...}}`

### Requirement: Order cancellation

An order in a non-terminal status (`PENDING`, `SUBMITTED`, `OPEN`, `PARTIALLY_FILLED`) SHALL be cancellable via `DELETE /api/v1/orders/{id}`, setting its status to `CANCELLED`. Cancelling a terminal order (`FILLED`, `CANCELLED`, `REJECTED`) SHALL be rejected with `400` code `not_cancellable`.

#### Scenario: Cancel a resting limit order
- **WHEN** a user cancels a resting (`OPEN`) limit order
- **THEN** the response is `200` with `status: CANCELLED`

#### Scenario: Cancel a filled order rejected
- **WHEN** a user cancels an already-`FILLED` order
- **THEN** the system responds `400` with `{"error": {"code": "not_cancellable", ...}}`

### Requirement: Fill ledgering (position and cash)

Each fill SHALL be applied to a signed-quantity position (positive = long, negative = short) with a weighted-average cost basis. A fill that increases a position SHALL update the average price without realizing PnL. A fill that reduces, closes, or flips a position SHALL realize PnL = `(fill_price - avg_price) * closing_quantity * direction`, accumulating into `realized_pnl`; a flip SHALL reset the average price to the fill price. Each fill SHALL debit/credit cash by `-signed_qty * fill_price` (buys decrease cash, sells increase cash) and record both a `Fill` row and a `trade` `BalanceTransaction` with the resulting `balance_after`.

#### Scenario: Opening a long position debits cash
- **WHEN** a user buys `10` at fill price `avg`
- **THEN** the position is `+10` at `avg` and `cash_balance` decreases by `10 * avg`

#### Scenario: Closing part of a position realizes no PnL drift in cash beyond price
- **WHEN** a user sells `4` of a `10`-long position
- **THEN** the position becomes `+6` and cash increases by `4 * fill_price`

### Requirement: Per-account execution isolation

Orders SHALL be computed and filled independently per `account_id`. One account's order SHALL NOT match against another account's book or affect another account's positions or cash. The simulated `SIM` venue is shared across calculations but account state is partitioned by `account_id` in PostgreSQL.

#### Scenario: Orders do not cross accounts
- **WHEN** two accounts each place orders on the same symbol
- **THEN** each account's fills, positions, and cash reflect only its own orders

