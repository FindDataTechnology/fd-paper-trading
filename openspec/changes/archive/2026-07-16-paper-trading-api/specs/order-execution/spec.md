## ADDED Requirements

### Requirement: Simulated execution with no real capital
The system SHALL execute all paper orders through a simulated execution engine (Nautilus `nautilus-sandbox`) that fills against live market data. The system MUST NOT route orders to any real broker and MUST NOT place real capital at risk.

#### Scenario: Order fills without broker
- **WHEN** a valid market order is submitted
- **THEN** it is filled by the simulated execution engine against the live market-data feed, and no external broker is contacted

### Requirement: Market order fills
The system SHALL fill market orders at the current best quote for the instrument. A market buy fills at the ask; a market sell fills at the bid.

#### Scenario: Market buy fills at ask
- **WHEN** a market buy for 10 shares is submitted while the best ask is 150.00
- **THEN** the order fills at 150.00 and the account's position and cash balance update accordingly

### Requirement: Limit order fills
The system SHALL fill a limit order when the market price crosses the limit price: a buy limit fills when the ask is at or below the limit; a sell limit fills when the bid is at or above the limit. Until then the limit order rests as open.

#### Scenario: Limit buy fills on touch
- **WHEN** a limit buy with limit 145.00 rests open and the ask drops to 145.00 or below
- **THEN** the order fills at the limit price (or better)

#### Scenario: Limit buy rests when not touched
- **WHEN** a limit buy with limit 145.00 is submitted while the ask is 150.00
- **THEN** the order remains open and unfilled, and appears in the account's open orders

### Requirement: Order cancellation
The system SHALL allow cancellation of a resting (open) order. A cancellation MUST NOT affect already-filled quantity. Orders that are already fully filled MUST NOT be cancellable.

#### Scenario: Cancel a resting order
- **WHEN** a user cancels an open limit order
- **THEN** the order is cancelled, no longer rests in the book, and its reserved buying power is released

#### Scenario: Cancel a filled order fails
- **WHEN** a user attempts to cancel an already-fully-filled order
- **THEN** the system rejects the cancellation with a clear error

### Requirement: Position and balance update on fill
The system SHALL update the account's position and cash balance immediately upon each fill, and SHALL persist the updated state to PostgreSQL.

#### Scenario: Fill updates position and cash
- **WHEN** a fill occurs for an account
- **THEN** the account's position in that instrument and its cash balance are updated and persisted before the fill is reported as complete

### Requirement: Fill persistence (event-sourced)
The system SHALL record every order and fill as an immutable event in PostgreSQL. The sequence of events MUST be sufficient to reconstruct account state.

#### Scenario: Fills are recorded immutably
- **WHEN** an order fills in two partial fills
- **THEN** two distinct fill events are appended to persistence, each with timestamp, price, quantity, and account reference

### Requirement: Execution is per-account isolated
The simulated execution engine SHALL maintain independent order books and account state per paper account (via per-account simulated venues). One account's orders MUST NOT match against another account's book or affect another account's state.

#### Scenario: No cross-account matching
- **WHEN** account X and account Y both have resting limit orders on the same symbol
- **THEN** the orders rest in their respective per-account books and do not match each other
