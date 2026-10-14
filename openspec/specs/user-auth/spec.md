# user-auth Specification

## Purpose

约束用户认证：注册/登录（JWT）、可吊销长命 API token（fdt_ 前缀、只存哈希、吊销即时生效）与令牌分流。
## Requirements
### Requirement: User registration

The system SHALL allow a new user to register with an email and a password (8-128 chars). Passwords MUST be hashed with bcrypt before storage; the plaintext password MUST never be persisted or returned. A duplicate email MUST be rejected with HTTP `409` and error code `email_exists`. The response SHALL be HTTP `201` with the user's `id`, `email`, and `created_at` (no password fields).

#### Scenario: Successful registration
- **WHEN** a client `POST /api/v1/auth/register` with a valid email and a password of at least 8 characters
- **THEN** the system responds `201` with `email`, `id`, and `created_at`, and omits any `password` / `password_hash` field

#### Scenario: Duplicate email rejected
- **WHEN** a client registers with an email that already exists
- **THEN** the system responds `409` with `{"error": {"code": "email_exists", ...}}`

#### Scenario: Invalid input rejected
- **WHEN** a client registers with a password shorter than 8 characters
- **THEN** the system responds `422` with error code `validation_error` and a non-empty `fields` array

### Requirement: User login issues a JWT access token

The system SHALL authenticate a user by email + password and, on success, return HTTP `200` with a bearer JWT access token, `token_type="bearer"`, `expires_in` (seconds), and `user_id`. Invalid credentials MUST return `401` with code `invalid_credentials`. An inactive user account MUST return `403` with code `user_inactive`.

#### Scenario: Successful login
- **WHEN** a registered user `POST /api/v1/auth/login` with the correct password
- **THEN** the system responds `200` with a non-empty `access_token`, `token_type="bearer"`, `expires_in > 0`, and a valid UUID `user_id`

#### Scenario: Wrong password
- **WHEN** a client logs in with an incorrect password
- **THEN** the system responds `401` with `{"error": {"code": "invalid_credentials", ...}}`

### Requirement: JWT access tokens

Access tokens MUST be HS256-signed JWTs carrying `iss`, `sub` (user id), `iat`, `exp`, and `type="access"` claims, with expiry bounded by `JWT_EXPIRY_MINUTES`. A token missing `exp`/`sub`/`type`, with `type != "access"`, or with an invalid signature MUST fail to decode.

#### Scenario: Valid token authenticates
- **WHEN** a request carries `Authorization: Bearer <valid-jwt>`
- **THEN** the requesting user is resolved and the request is authorized

#### Scenario: Expired or malformed token rejected
- **WHEN** a request carries a malformed or expired JWT
- **THEN** the system responds `401` with `{"error": {"code": "unauthorized", ...}}` (or `invalid`/`expired credentials`)

### Requirement: Revocable API tokens

A authenticated user MAY create a long-lived API token (`POST /api/v1/tokens`, `201`) whose plaintext starts with `fdt_`; the plaintext is returned **exactly once** at creation and only a SHA-256 hash is stored. The system SHALL accept an API token as a bearer credential interchangeably with a JWT. `GET /api/v1/tokens` lists the user's tokens (never returning the plaintext). `DELETE /api/v1/tokens/{id}` revokes a token (`204`); a revoked token MUST no longer authenticate (`401`). Revoking an API token MUST NOT invalidate the user's JWTs.

#### Scenario: Create, use, list, and revoke a token
- **WHEN** a user creates an API token
- **THEN** the `201` response includes the `fdt_`-prefixed plaintext and an `id`
- **AND WHEN** the user lists tokens
- **THEN** each listed token has `token: null`
- **AND WHEN** the plaintext token is used as a bearer credential
- **THEN** the request authenticates successfully
- **AND WHEN** the token is revoked via `DELETE`
- **THEN** the response is `204` and subsequent use of the plaintext token returns `401`

#### Scenario: Revoking an API token leaves JWTs valid
- **WHEN** a user revokes one API token
- **THEN** the user's existing JWT continues to authenticate while the revoked API token does not

### Requirement: Bearer authentication on protected endpoints

All endpoints except `/health` and the `auth/register` / `auth/login` endpoints SHALL require an `Authorization: Bearer <token>` header. A missing, malformed, revoked, or expired credential MUST return `401`. The dependency SHALL accept either a JWT or an `fdt_`-prefixed API token and resolve the requesting `User`; inactive users MUST be rejected.

#### Scenario: Missing bearer token
- **WHEN** a client calls a protected endpoint without an `Authorization` header
- **THEN** the system responds `401` with `{"error": {"code": "unauthorized", ...}}`

#### Scenario: Invalid bearer token
- **WHEN** a client calls a protected endpoint with a revoked or malformed token
- **THEN** the system responds `401`

