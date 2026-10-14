# web-realtime Specification

## Purpose

约束 Web 实时通道：/ws 订阅、报价/订单/成交事件的推送与 UI 状态同步。
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
- **WHEN** an authenticated client sends a subscribe message for a symbol
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
