## MODIFIED Requirements

### Requirement: Account reset

`POST /api/v1/accounts/{id}/reset` SHALL cancel all open orders (setting status `CANCELLED` with reason `"account reset"`), flatten all positions at cost basis (returning each position's cost to cash with zero realized PnL), clear every position lot for the account so no sellable or unsellable batch survives the reset, and restore `cash_balance` to the provided `starting_balance` (or the account's existing `starting_balance` if omitted). A `reset` `BalanceTransaction` SHALL be recorded.

#### Scenario: Reset restores starting balance
- **WHEN** a user resets an account (starting balance `100000`) after depositing `5000`
- **THEN** `cash_balance` returns to `100000`

#### Scenario: Reset clears position lots
- **WHEN** a user resets an account holding positions bought across several days
- **THEN** no position lot remains for that account
- **AND THEN** a subsequent same-day buy is again unsellable until the next day
