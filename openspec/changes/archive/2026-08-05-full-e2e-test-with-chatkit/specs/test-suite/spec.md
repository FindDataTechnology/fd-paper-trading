## MODIFIED Requirements

### Requirement: Backend end-to-end tests via the ASGI transport

The system SHALL provide a `tests/e2e/` suite that drives the full FastAPI application through httpx's ASGI transport against the simulated market-data provider and an in-memory SQLite database, requiring no external services. The suite SHALL cover the complete user journey (register -> login -> create account -> search symbol -> fetch bars -> open WebSocket and subscribe -> place order -> receive order/fill event over `/ws` -> position appears), the limit-order placement outcomes (a non-crossing limit rests as `OPEN` and is then cancellable; a crossing limit fills immediately at placement), account reset after trades, multi-account and cross-user isolation, the API-token authentication flow (create token, use it in place of a JWT, revoke it), the bars/search contract (supported timeframes, oldest-first ordering, unknown timeframe rejected), and the chat endpoint (authentication, tool invocation, error handling). Because v1 computes fills statelessly per order, the suite SHALL NOT assert that a resting limit later fills on its own.

#### Scenario: Full trading flow over HTTP and WebSocket
- **WHEN** a client registers, logs in, creates an account, subscribes to a symbol over `/ws`, and places a market `BUY`
- **THEN** the order returns `202` with status `FILLED`, an `order` event arrives over `/ws`, and a position for the symbol appears in the account

#### Scenario: Revoked API token is rejected
- **WHEN** a user creates an API token, uses it successfully, then revokes it and retries a protected request
- **THEN** the post-revoke request returns `401`

#### Scenario: Chat endpoint requires authentication
- **WHEN** a POST request to `/api/v1/chat` is made without authentication
- **THEN** the response status is `401`

#### Scenario: Chat tool invocation respects account ownership
- **WHEN** a user asks the chat assistant to access another user's account
- **THEN** the tool returns an error and no data is leaked

## MODIFIED Requirements

### Requirement: Frontend end-to-end test

The system SHALL provide a Playwright end-to-end test under `web/e2e/` that builds the SPA and drives a real browser against the running FastAPI backend through one full journey: register a new user -> log in -> create a paper account -> search a symbol -> place a market order -> observe the fill and resulting position in the UI -> cancel an order -> open the chat interface -> send a message -> receive an AI response. The test SHALL run headless and require no manual setup beyond starting the backend and building the SPA.

#### Scenario: Happy-path trading journey in a browser
- **WHEN** the Playwright suite runs against the built SPA and backend
- **THEN** a user can register, log in, create an account, place an order, see the position appear, cancel an order, open the chat, send a message, and receive an AI response, all through the real UI
