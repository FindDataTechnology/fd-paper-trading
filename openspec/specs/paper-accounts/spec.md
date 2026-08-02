# paper-accounts Specification

## Purpose
TBD - created by archiving change paper-trading-system. Update Purpose after archive.
## Requirements
### Requirement: Multi-account ownership

A user MAY own zero or more paper trading accounts. Each account SHALL be scoped to exactly one owning user and isolated from all other users' accounts. Each account SHALL have a unique simulated venue identifier of the form `PAPER-<12-hex>` and a currency (default `USD`).

#### Scenario: A user creates multiple accounts
- **WHEN** a user creates two accounts (e.g. `acct1`, `acct2`)
- **THEN** `GET /api/v1/accounts` returns both accounts for that user only

#### Scenario: Venue is assigned on creation
- **WHEN** a user creates an account
- **THEN** the account's `venue` starts with `PAPER-`

### Requirement: Account creation with starting balance

`POST /api/v1/accounts` SHALL create an account with a `name` (default `"default"`) and an optional `starting_balance` (>= 0, default `100000.0000` in the configured account currency). The `cash_balance` SHALL equal the starting balance at creation, and an initial `deposit` `BalanceTransaction` SHALL be recorded. The response SHALL be `201` with `id`, `name`, `venue`, `currency`, `starting_balance`, `cash_balance`, and `created_at`.

#### Scenario: Default starting balance
- **WHEN** a user creates an account without a `starting_balance`
- **THEN** `cash_balance` is `100000` and `name` is `"default"`

#### Scenario: Custom starting balance
- **WHEN** a user creates an account with `starting_balance: 50000`
- **THEN** `cash_balance` is `50000`

### Requirement: Account summary

`GET /api/v1/accounts/{id}` SHALL return a summary: `cash_balance`, `equity`, `open_positions` (count of positions with non-zero quantity), and `open_orders` (count of non-terminal orders). Until positions are marked to market, `equity` SHALL equal `cash_balance`.

#### Scenario: Fresh account summary
- **WHEN** a user fetches a freshly created account
- **THEN** `open_positions` is `0`, `open_orders` is `0`, and `equity` equals `cash_balance`

### Requirement: Signed deposit and withdrawal

`POST /api/v1/accounts/{id}/deposit` SHALL accept a signed `amount`: positive for deposit, negative for withdrawal. A zero amount MUST be rejected. A withdrawal that would drive `cash_balance` below zero MUST be rejected with `400` and code `invalid_amount`. Each operation SHALL record a `deposit` or `withdrawal` `BalanceTransaction` with the resulting `balance_after`.

#### Scenario: Deposit increases cash
- **WHEN** a user deposits `5000` into an account with `100000` cash
- **THEN** `cash_balance` becomes `105000`

#### Scenario: Withdrawal decreases cash
- **WHEN** a user deposits `-3000` into an account with `105000` cash
- **THEN** `cash_balance` becomes `102000`

#### Scenario: Over-withdrawal rejected
- **WHEN** a user attempts to withdraw more than the cash balance
- **THEN** the system responds `400` with `{"error": {"code": "invalid_amount", ...}}`

### Requirement: Account reset

`POST /api/v1/accounts/{id}/reset` SHALL cancel all open orders (setting status `CANCELLED` with reason `"account reset"`), flatten all positions at cost basis (returning each position's cost to cash with zero realized PnL), and restore `cash_balance` to the provided `starting_balance` (or the account's existing `starting_balance` if omitted). A `reset` `BalanceTransaction` SHALL be recorded.

#### Scenario: Reset restores starting balance
- **WHEN** a user resets an account (starting balance `100000`) after depositing `5000`
- **THEN** `cash_balance` returns to `100000`

### Requirement: Per-user and per-account isolation

Every account, position, and order lookup SHALL filter by the requesting user's identity (and account ownership). Accessing another user's account, depositing into it, placing an order against it, or reading its positions/orders SHALL return `404` with code `not_found` (not `403`), so ownership is not leaked.

#### Scenario: Cross-user account access denied
- **WHEN** user Bob reads or deposits into Alice's account
- **THEN** the system responds `404` with `{"error": {"code": "not_found", ...}}`

#### Scenario: Other user's account list is empty
- **WHEN** Bob (who owns no accounts) lists his accounts
- **THEN** the response is an empty array

### Requirement: Position and order reads per account

`GET /api/v1/accounts/{id}/positions` SHALL return only open positions (non-zero quantity) for an owned account. `GET /api/v1/accounts/{id}/orders` SHALL return the account's orders, optionally filtered by `?status=`. Both SHALL return `404` for an account the user does not own.

#### Scenario: Empty positions and orders on a fresh account
- **WHEN** a user lists positions and orders for a fresh account
- **THEN** both responses are `200` with an empty array

