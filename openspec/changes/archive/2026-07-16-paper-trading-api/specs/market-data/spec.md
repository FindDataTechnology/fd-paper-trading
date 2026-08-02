## ADDED Requirements

### Requirement: Real-time market data ingestion
The system SHALL ingest real-time stock market data (quotes, trades, and/or bars) from an external provider via a Nautilus data client and SHALL make it available to the execution engine and API clients. No broker connection is required for data.

#### Scenario: Live quotes flow into the engine
- **WHEN** the data client is connected and subscribed to a symbol
- **THEN** real-time quote/trade updates for that symbol are received and routed to the Nautilus cache and the simulated execution engine

### Requirement: Instrument loading into cache
The system SHALL load instrument definitions for subscribed symbols into the Nautilus cache before trading. If an instrument has not loaded, the system MUST NOT accept orders for it.

#### Scenario: Order rejected for unknown instrument
- **WHEN** an order is submitted for a symbol whose instrument is not in the cache
- **THEN** the system rejects the order with a clear error and does not submit it to the execution engine

### Requirement: Server-side subscription fan-out
The system SHALL subscribe to each symbol at most once per data provider and fan out the stream to all interested accounts and API clients. The system MUST enforce per-user symbol-subscription limits to control provider costs and rate limits.

#### Scenario: Multiple clients share one upstream subscription
- **WHEN** two clients subscribe to the same symbol
- **THEN** the system maintains a single upstream provider subscription and delivers updates to both clients

#### Scenario: Subscription limit enforced
- **WHEN** a user attempts to subscribe to more symbols than their configured limit allows
- **THEN** the system rejects the additional subscription with a clear error

### Requirement: Market data streaming to API clients
The system SHALL stream real-time market data to authenticated API clients over WebSocket, scoped to symbols the client has subscribed to.

#### Scenario: Client receives streamed ticks
- **WHEN** a client holds an open WebSocket subscription for a symbol and a new quote arrives
- **THEN** the system pushes the update to that client's WebSocket connection

### Requirement: Data gap handling
The system SHALL detect when fresh market data is unavailable for a symbol and SHALL reject new market orders for that symbol until a fresh quote is available. Limit orders MUST be queued and filled on the next valid quote.

#### Scenario: Market order rejected during data gap
- **WHEN** a market order is submitted for a symbol with no recent quote
- **THEN** the system rejects the order with a "no live quote" error and does not fill it

#### Scenario: Limit order waits for data
- **WHEN** a limit order is submitted during a data gap and a valid quote later arrives at the limit price
- **THEN** the system fills the order at that point
