## MODIFIED Requirements

### Requirement: User groups with config

The system SHALL persist user groups: `user_groups(id, name unique, config JSONB, created_at)`. Recognized config keys with defaults: `max_active_strategies` (3), `max_backtest_days` (730), `max_orders_per_day` (20), `allow_custom_code` (false), `allowed_symbols` (null = all symbols), `max_subscriptions` (50, where `0` means unlimited). Every user SHALL belong to exactly one group; a migration SHALL seed a `default` group with the defaults and assign all existing users to it.

#### Scenario: Default group seeded
- **WHEN** the migration runs on an existing database
- **THEN** a `default` group exists and every pre-existing user belongs to it

#### Scenario: Missing config keys fall back to defaults
- **WHEN** a group's config omits `max_active_strategies`
- **THEN** the effective value is 3

### Requirement: Limits enforced at the API layer

Group limits SHALL be enforced at these points: starting a paper run (`max_active_strategies` concurrent runs per user), requesting a backtest (`max_backtest_days` window), strategy-run order placement (`max_orders_per_day` per run per day), strategy create/update (`allow_custom_code` gate, `allowed_symbols` allowlist when non-null), and `/ws` quote subscription (`max_subscriptions` distinct symbols per user across all of that user's connections). Enforcement SHALL produce clear errors naming the limit.

#### Scenario: Symbol allowlist enforced
- **WHEN** a user whose group has `allowed_symbols: ["AAPL"]` creates a strategy on `TSLA`
- **THEN** the system responds `400`/`403` naming the allowed symbols

#### Scenario: Backtest window enforced
- **WHEN** a user whose group allows 730 backtest days requests a 5-year window
- **THEN** the system responds `400` naming the limit

#### Scenario: Subscription cap enforced over the WebSocket
- **WHEN** a user exceeds the group's `max_subscriptions` distinct symbols
- **THEN** the server rejects the extra subscribe with an error event whose `code` is `subscription_limit` and whose message names the limit
