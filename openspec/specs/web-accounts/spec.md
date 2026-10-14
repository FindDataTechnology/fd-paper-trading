# web-accounts Specification

## Purpose

约束 Web 端账户面：账户切换、开户入口、入金/重置交互与展示。
## Requirements

### Requirement: Account dashboard
The system SHALL provide an account dashboard that shows the selected account's summary - cash balance, equity, open positions count, and open orders count - consuming `GET /api/v1/accounts/{id}`.

#### Scenario: View account summary
- **WHEN** a user selects an account
- **THEN** the SPA displays the account's cash, equity, open positions count, and open orders count

#### Scenario: Equity includes position market value
- **WHEN** the account has open positions and live quotes are available
- **THEN** the SPA computes equity as cash plus the sum of position market values and displays it as indicative

### Requirement: Positions table with unrealized P&L
The system SHALL display the selected account's open positions with symbol, quantity, average price, and unrealized P&L, consuming `GET /api/v1/accounts/{id}/positions` and computing P&L client-side from the live quote.

#### Scenario: List positions
- **WHEN** a user views the positions table for an account
- **THEN** the SPA displays each position's symbol, quantity, average price, and unrealized P&L

#### Scenario: P&L updates with live quote
- **WHEN** a quote tick arrives for a held symbol
- **THEN** the SPA updates that position's unrealized P&L in place

#### Scenario: No positions
- **WHEN** the account has no open positions
- **THEN** the SPA shows an empty-state message

### Requirement: Account list and switcher
The system SHALL list the user's accounts and let the user switch the active account, consuming `GET /api/v1/accounts`.

#### Scenario: List accounts
- **WHEN** a user opens the account switcher
- **THEN** the SPA displays all accounts owned by the user

#### Scenario: Switch active account
- **WHEN** a user selects a different account
- **THEN** the SPA updates the active account and refreshes the dashboard, positions, and orders views for that account

### Requirement: Account management actions
The system SHALL let the user deposit into and reset an account, consuming `POST /api/v1/accounts/{id}/deposit` and `POST /api/v1/accounts/{id}/reset`.

#### Scenario: Deposit funds
- **WHEN** a user submits a deposit amount for an account
- **THEN** the SPA calls the deposit endpoint and refreshes the account summary

#### Scenario: Reset an account
- **WHEN** a user confirms an account reset with a starting balance
- **THEN** the SPA calls the reset endpoint and refreshes the account summary

#### Scenario: Invalid amount is rejected
- **WHEN** a user submits a non-positive deposit amount
- **THEN** the SPA disables submission and shows a validation error before calling the API
