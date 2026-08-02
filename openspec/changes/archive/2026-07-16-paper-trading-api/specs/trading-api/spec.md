## ADDED Requirements

### Requirement: Versioned REST API
The system SHALL expose all REST endpoints under an `/api/v1` prefix so future changes can be introduced without breaking existing clients.

#### Scenario: Endpoints are versioned
- **WHEN** a client inspects available routes
- **THEN** every trading/account/auth REST endpoint is rooted under `/api/v1`

### Requirement: Place order endpoint
The system SHALL provide an authenticated REST endpoint to place a market or limit order for a stock on a specified account owned by the caller.

#### Scenario: Place a market order
- **WHEN** an authenticated user POSTs a valid market order (account, symbol, side, quantity) to the order endpoint
- **THEN** the system accepts the order, returns `202 Accepted` with an order identifier, and routes it to the simulated execution engine

#### Scenario: Place a limit order
- **WHEN** an authenticated user POSTs a valid limit order (account, symbol, side, quantity, limit price)
- **THEN** the system accepts the order, returns `202 Accepted` with an order identifier, and rests it in the execution engine until fill or cancel

#### Scenario: Reject order for another user's account
- **WHEN** a user POSTs an order referencing an account they do not own
- **THEN** the system returns `404 Not Found` and does not submit the order

### Requirement: Cancel order endpoint
The system SHALL provide an authenticated REST endpoint to cancel an open order owned by the caller.

#### Scenario: Cancel an open order
- **WHEN** an authenticated user POSTs a cancellation for one of their open orders
- **THEN** the system cancels the order and returns its final status

### Requirement: Order and position query endpoints
The system SHALL provide authenticated REST endpoints to query a single order, list an account's orders (filterable by status), and list an account's open positions.

#### Scenario: List open orders
- **WHEN** an authenticated user requests the open orders for one of their accounts
- **THEN** the system returns only that account's open orders

#### Scenario: Query a single order
- **WHEN** an authenticated user requests an order by identifier
- **THEN** the system returns the order's status, fills, and metadata, provided it belongs to the caller

### Requirement: Account management endpoints
The system SHALL provide authenticated REST endpoints to list the caller's accounts, create an account, query an account summary, and reset an account.

#### Scenario: Create and then list an account
- **WHEN** a user creates an account and then lists their accounts
- **THEN** the new account appears in the list with its initial balance

### Requirement: WebSocket streaming
The system SHALL expose an authenticated WebSocket endpoint that streams real-time market data for subscribed symbols and order/fill updates for the caller's accounts.

#### Scenario: Subscribe to symbols and account updates
- **WHEN** an authenticated client opens the WebSocket and subscribes to symbols and their account order stream
- **THEN** the client receives live market-data updates for those symbols and order/fill events for their accounts

### Requirement: Authentication on all endpoints
The system SHALL require valid authentication (JWT or API token) on every trading, account, and subscription endpoint. Unauthenticated requests MUST be rejected before any trading action.

#### Scenario: No token, no trade
- **WHEN** a client calls the place-order endpoint without a valid token
- **THEN** the system returns `401 Unauthorized` and no order is submitted

### Requirement: Standardized error responses
The system SHALL return errors as a consistent JSON structure containing at least an error code, a human-readable message, and, where applicable, the offending field(s).

#### Scenario: Validation error response
- **WHEN** a client POSTs an order with a missing or invalid field
- **THEN** the system returns `400 Bad Request` with a JSON body identifying the error code, message, and offending field

### Requirement: Asynchronous order lifecycle reporting
Because fills happen on the execution loop, the place-order endpoint SHALL return `202 Accepted` with an order identifier, and final fill/status SHALL be delivered via the WebSocket stream and/or queryable via the order query endpoint.

#### Scenario: Final status delivered after acceptance
- **WHEN** an order is accepted and subsequently fills
- **THEN** the fill is delivered over the caller's WebSocket stream and is also retrievable via the order query endpoint
