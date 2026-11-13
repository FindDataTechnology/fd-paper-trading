# web-realtime Specification

## Purpose

约束 Web 实时通道：/ws 连接生命周期、报价/订单/成交三类事件的推送语义、引用计数订阅（按 tab 作用域）与 UI 状态同步。

## Requirements

### Requirement: WebSocket streaming endpoint
The system SHALL expose an authenticated WebSocket endpoint at `/ws` that streams quote, order-status, and fill events to connected clients, scoped to the authenticated user's accounts and subscriptions.

#### Scenario: Authenticate on connect
- **WHEN** a client connects to `/ws` and sends a valid JWT in the first message
- **THEN** the system accepts the connection and begins streaming events for that user

#### Scenario: Reject unauthenticated connection
- **WHEN** a client connects but does not send a valid token in the first message within a short window
- **THEN** the system closes the connection without streaming any events

#### Scenario: Subscribe to a symbol's quotes
- **WHEN** an authenticated client sends a subscribe message for a symbol it has not exceeded the cap on
- **THEN** the system starts sending that client quote ticks for that symbol

#### Scenario: Unsubscribe from a symbol
- **WHEN** a client sends an unsubscribe message for a symbol
- **THEN** the system stops sending quote ticks for that symbol to that client

#### Scenario: Order and fill events are scoped to the user
- **WHEN** an order owned by the authenticated user changes status or is filled
- **THEN** the system pushes the corresponding order-status or fill event to that user's connection, and never to other users

### Requirement: Frontend WebSocket client
The system SHALL maintain a single WebSocket connection in the SPA that authenticates on connect, auto-reconnects with backoff on drop, and re-establishes subscriptions after reconnect, exposing live events to the order, chart, and accounts views.

#### Scenario: Connection authenticates and subscribes
- **WHEN** the SPA starts with a valid token
- **THEN** it opens `/ws`, sends the token, and re-subscribes to the active symbol and the user's order/fill stream

#### Scenario: Auto-reconnect on drop
- **WHEN** the WebSocket connection drops
- **THEN** the SPA reconnects with exponential backoff and restores prior subscriptions after reconnecting

#### Scenario: Quote ticks update subscribers
- **WHEN** a quote tick arrives for a symbol the UI is viewing
- **THEN** the SPA dispatches the tick to the chart and positions views that depend on it

### Requirement: Simulated quote tick source
The system SHALL emit periodic quote ticks from the `SimulatedMarketDataProvider` so the streaming contract is exercised end-to-end before a real feed is integrated.

#### Scenario: Ticks flow for a subscribed symbol
- **WHEN** a client is subscribed to a symbol served by the simulated provider
- **THEN** the system emits quote ticks for that symbol at a regular interval

### Requirement: Bounded quote subscriptions

The system SHALL cap the number of distinct symbols a user may subscribe to across all of that user's `/ws` connections, using the group-config key `max_subscriptions` (default 50, `0` = unlimited). The cap SHALL be counted per user, not per connection: opening an additional connection SHALL NOT grant an additional allowance. A subscribe message that would exceed the cap for a symbol the user is not already subscribed to SHALL be rejected with an error event naming the code `subscription_limit`, the limit, and the symbol, and SHALL leave the connection and its existing subscriptions intact. Re-subscribing to a symbol the user already holds SHALL succeed even at the cap, because clients restore their subscriptions after reconnecting. The quote tick cycle SHALL fetch quotes with bounded concurrency so that a legal number of subscriptions cannot starve the cycle past its interval.

#### Scenario: Subscribing past the cap is refused and explained
- **WHEN** a user whose group allows 50 subscriptions already holds 50 distinct symbols and sends a subscribe message for a 51st
- **THEN** no subscription is added for that symbol
- **AND THEN** the server sends `{"type": "error", "code": "subscription_limit", "limit": 50, "symbol": "<the 51st>"}`
- **AND THEN** the connection stays open and the first 50 subscriptions keep streaming

#### Scenario: The cap is per user, not per connection
- **WHEN** a user holds 50 symbols on one connection and opens a second connection for a 51st symbol
- **THEN** the second connection's subscribe is refused with `subscription_limit`
- **AND WHEN** that user unsubscribes one symbol on the first connection
- **THEN** subscribing to the new symbol on the second connection now succeeds

#### Scenario: Re-subscribing an already-held symbol is idempotent at the cap
- **WHEN** a user at the cap re-sends a subscribe message for a symbol already in their set
- **THEN** the message succeeds with no error event
- **AND THEN** the distinct-symbol count is unchanged

#### Scenario: Zero disables the cap
- **WHEN** the user's group sets `max_subscriptions: 0`
- **THEN** subscribe messages are accepted without a limit

#### Scenario: Tick fetching is concurrent but bounded
- **WHEN** many symbols are subscribed and the provider's `get_quote` is slow
- **THEN** the tick cycle issues several quote requests concurrently
- **AND THEN** it never has more than the configured fetch-concurrency limit in flight at once
