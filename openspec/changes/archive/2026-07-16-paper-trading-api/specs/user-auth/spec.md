## ADDED Requirements

### Requirement: User registration
The system SHALL allow a new user to register with a unique identifier (email) and a password. Passwords MUST NOT be stored in plaintext.

#### Scenario: Successful registration
- **WHEN** a client POSTs a valid email and password to the registration endpoint
- **THEN** the system creates a user record with a bcrypt-hashed password and returns the new user's identifier with a `201 Created` status

#### Scenario: Duplicate email rejected
- **WHEN** a client registers with an email that already exists
- **THEN** the system rejects the request with a `409 Conflict` and does not create a second user

### Requirement: Password hashing
The system SHALL hash all user passwords with bcrypt before storage and MUST NEVER store or log plaintext passwords.

#### Scenario: Password is hashed at rest
- **WHEN** a user registers or changes their password
- **THEN** only a bcrypt hash is persisted, and the plaintext password is not present in any log or database column

### Requirement: JWT access token issuance
The system SHALL issue a short-lived JWT access token to a user who authenticates with valid credentials.

#### Scenario: Successful login
- **WHEN** a client POSTs valid email and password to the login endpoint
- **THEN** the system returns a JWT access token bound to that user with a bounded expiry

#### Scenario: Invalid credentials rejected
- **WHEN** a client POSTs an incorrect password for a known email
- **THEN** the system returns `401 Unauthorized` and issues no token

### Requirement: Revocable API tokens
The system SHALL allow an authenticated user to create and revoke long-lived API tokens for programmatic order placement. API tokens MUST be revocable independently of the user's password.

#### Scenario: Create an API token
- **WHEN** an authenticated user requests a new API token
- **THEN** the system issues a long-lived token, stores only a hashed form of it, and returns the plaintext token exactly once to the user

#### Scenario: Revoke an API token
- **WHEN** a user revokes one of their API tokens
- **THEN** subsequent requests carrying that token are rejected with `401 Unauthorized`, while the user's other tokens and JWT remain valid

### Requirement: Request authentication and authorization
The system SHALL authenticate every protected API request using either a valid JWT or a valid API token, and SHALL identify the requesting user for all subsequent operations.

#### Scenario: Authenticated request is accepted
- **WHEN** a request carries a valid JWT or API token
- **THEN** the system resolves the requesting user and processes the request under that user's identity

#### Scenario: Unauthenticated request is rejected
- **WHEN** a protected request carries no token or an invalid/expired token
- **THEN** the system rejects it with `401 Unauthorized` and performs no trading action

### Requirement: User isolation
The system SHALL ensure a user can only access resources (accounts, orders, positions, tokens) belonging to themselves. A user MUST NOT be able to read or mutate another user's resources.

#### Scenario: Cross-user access is denied
- **WHEN** an authenticated user requests an account or order owned by a different user
- **THEN** the system returns `404 Not Found` (or `403 Forbidden`) and does not disclose or modify the resource
