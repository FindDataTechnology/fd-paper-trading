## ADDED Requirements

### Requirement: Multiple accounts per user
The system SHALL allow a user to own more than one paper trading account, each with its own identifier, cash balance, positions, and order history. Accounts MUST be scoped to their owning user.

#### Scenario: Create a second account
- **WHEN** an authenticated user requests creation of a new paper account and they already own one
- **THEN** the system creates an additional, independent account and returns its identifier

#### Scenario: Accounts are isolated between users
- **WHEN** user A creates an account
- **THEN** user B cannot see, select, or trade in user A's account

### Requirement: Cash balance management
Each paper account SHALL have a cash balance denominated in a reporting currency. The system MUST support setting an initial balance at creation and adjusting the balance via paper deposits/withdrawals. No real funds are involved.

#### Scenario: Initial balance on creation
- **WHEN** a user creates an account with a specified starting balance
- **THEN** the account's cash balance equals that amount and is persisted

#### Scenario: Paper deposit increases balance
- **WHEN** a user deposits paper cash into an account
- **THEN** the account's cash balance increases by the deposited amount and the transaction is recorded

### Requirement: Per-account state isolation
Positions, open orders, and balances MUST be isolated per account. An order or fill in one account MUST NOT affect the balance or positions of any other account.

#### Scenario: Fill affects only the placing account
- **WHEN** a fill occurs for an order in account X
- **THEN** only account X's cash balance and positions change; account Y (same user or different) is unchanged

### Requirement: Account reset
The system SHALL allow a user to reset a paper account, which cancels open orders, flattens positions at current market, and restores the cash balance to a specified starting value.

#### Scenario: Reset clears state and restores balance
- **WHEN** a user resets an account with a target starting balance
- **THEN** all open orders for that account are cancelled, positions are flattened, and the cash balance is set to the target value, with the reset recorded in history

### Requirement: Account query
The system SHALL allow a user to list their accounts and query a single account's summary (balance, equity, open positions count, open orders count).

#### Scenario: List accounts
- **WHEN** an authenticated user requests their accounts
- **THEN** the system returns only the accounts owned by that user with their current balances

### Requirement: Durable account state
The system SHALL persist every account, balance change, position, and fill to PostgreSQL and MUST be able to rebuild an account's live state (balance, open positions, open orders) from persisted records on restart.

#### Scenario: State survives restart
- **WHEN** the service restarts after an account had an open position and an open order
- **THEN** on restart the account's balance, open position, and open order are restored from PostgreSQL into the live execution cache
