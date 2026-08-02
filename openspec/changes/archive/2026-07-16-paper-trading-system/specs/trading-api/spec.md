## ADDED Requirements

### Requirement: Versioned REST API under /api/v1

All trading endpoints SHALL be mounted under the `/api/v1` prefix. The service SHALL auto-serve OpenAPI docs at `/docs` and `/openapi.json`, and a `GET /health` endpoint returning `{"status": "ok"}` (unauthenticated).

#### Scenario: Health check
- **WHEN** a client calls `GET /health`
- **THEN** the system responds `200` with `{"status": "ok"}`

#### Scenario: OpenAPI is available
- **WHEN** a client calls `GET /docs` or `GET /openapi.json`
- **THEN** the system responds `200`

### Requirement: Standardized JSON error responses

Every error response SHALL use the shape `{"error": {"code": "...", "message": "...", "fields": [...]}}` (with `fields` omitted or empty when none). Request validation failures SHALL return `422` with code `validation_error` and a non-empty `fields` array (each `{"loc": [...], "message": "..."}`). Starlette `HTTPException`s SHALL be normalized to the same shape using a status-to-code map (`404 -> not_found`, `401 -> unauthorized`, `409 -> conflict`, etc.). Unknown routes SHALL return `404` with code `not_found` in the standardized shape.

#### Scenario: Validation error shape
- **WHEN** a client submits an invalid request body
- **THEN** the system responds `422` with `error.code == "validation_error"` and a non-empty `error.fields`

#### Scenario: Unknown route
- **WHEN** a client calls a non-existent `/api/v1/...` path
- **THEN** the system responds `404` with `{"error": {"code": "not_found", ...}}`

### Requirement: Authentication enforced on all endpoints

All `/api/v1` endpoints SHALL require a valid bearer credential (JWT or API token), except registration and login. Unauthenticated requests SHALL return `401` with code `unauthorized`.

#### Scenario: Protected endpoint without auth
- **WHEN** a client calls `GET /api/v1/accounts` with no `Authorization` header
- **THEN** the system responds `401` with `{"error": {"code": "unauthorized", ...}}`

### Requirement: Order placement, cancellation, and query endpoints

The API SHALL expose: `POST /api/v1/orders` (place a market or limit order; returns `202 Accepted` with the order, including any synchronous fills); `DELETE /api/v1/orders/{id}` (cancel; `200`); `GET /api/v1/orders/{id}` (query one; `200`). The order request SHALL validate that `side` is `BUY`/`SELL`, `order_type` is `MARKET`/`LIMIT`, `quantity > 0`, and that `LIMIT` orders include a `limit_price > 0`. Business rejections (unknown instrument, no quote, insufficient buying power, not cancellable) SHALL return `400` with the corresponding code.

#### Scenario: Place a market order returns 202
- **WHEN** a user `POST /api/v1/orders` a valid market buy
- **THEN** the system responds `202` with the order (status `FILLED` for a market buy) and its fills

#### Scenario: Limit order without limit price rejected
- **WHEN** a user submits a `LIMIT` order with no `limit_price`
- **THEN** the system responds `422` with `{"error": {"code": "validation_error", ...}}`

#### Scenario: Query an unknown order
- **WHEN** a user `GET /api/v1/orders/{random-uuid}`
- **THEN** the system responds `404` with `{"error": {"code": "not_found", ...}}`

### Requirement: Account, position, and order-list endpoints

The API SHALL expose account management under `/api/v1/accounts` (list `200`, create `201`, get-summary `200`, deposit `200`, reset `200`) and reads of `GET /api/v1/accounts/{id}/positions` and `GET /api/v1/accounts/{id}/orders` (optional `?status=` filter). All SHALL be scoped to the authenticated user and return `404` (`not_found`) for resources owned by another user.

#### Scenario: Cross-account order placement denied
- **WHEN** user Bob places an order against Alice's account
- **THEN** the system responds `404` with `{"error": {"code": "not_found", ...}}`

### Requirement: Auth and API-token endpoints

The API SHALL expose `POST /api/v1/auth/register` (`201`), `POST /api/v1/auth/login` (`200`), `POST /api/v1/tokens` (`201`, plaintext returned once), `GET /api/v1/tokens` (`200`, no plaintext), and `DELETE /api/v1/tokens/{id}` (`204`). Token management SHALL require authentication and be scoped to the requesting user.

#### Scenario: Revoke token returns 204
- **WHEN** a user `DELETE /api/v1/tokens/{id}` their own token
- **THEN** the system responds `204`
