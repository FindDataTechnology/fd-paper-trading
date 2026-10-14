# user-groups Specification

## Purpose

约束用户组限额：每组可配（活跃策略数/回测天数/日订单数/自定义代码门/标的白名单），默认组与注册挂组语义。
## Requirements
### Requirement: User groups with config

The system SHALL persist user groups: `user_groups(id, name unique, config JSONB, created_at)`. Recognized config keys with defaults: `max_active_strategies` (3), `max_backtest_days` (730), `max_orders_per_day` (20), `allow_custom_code` (false), `allowed_symbols` (null = all symbols). Every user SHALL belong to exactly one group; a migration SHALL seed a `default` group with the defaults and assign all existing users to it.

#### Scenario: Default group seeded
- **WHEN** the migration runs on an existing database
- **THEN** a `default` group exists and every pre-existing user belongs to it

#### Scenario: Missing config keys fall back to defaults
- **WHEN** a group's config omits `max_active_strategies`
- **THEN** the effective value is 3

### Requirement: Limits enforced at the API layer

Group limits SHALL be enforced at these points: starting a paper run (`max_active_strategies` concurrent runs per user), requesting a backtest (`max_backtest_days` window), strategy-run order placement (`max_orders_per_day` per run per day), and strategy create/update (`allow_custom_code` gate, `allowed_symbols` allowlist when non-null). Enforcement SHALL produce clear errors naming the limit.

#### Scenario: Symbol allowlist enforced
- **WHEN** a user whose group has `allowed_symbols: ["AAPL"]` creates a strategy on `TSLA`
- **THEN** the system responds `400`/`403` naming the allowed symbols

#### Scenario: Backtest window enforced
- **WHEN** a user whose group allows 730 backtest days requests a 5-year window
- **THEN** the system responds `400` naming the limit

### Requirement: Admin flag and group management API

Users SHALL have an `is_admin` flag (default false). The first admin(s) SHALL be bootstrapped via the `FD_ADMIN_EMAILS` environment variable (comma-separated emails granted `is_admin` at registration) or direct DB update. Admin-only endpoints: `GET /api/v1/groups`, `POST /api/v1/groups`, `PATCH /api/v1/groups/{id}`, `POST /api/v1/users/{id}/group`. Non-admin callers SHALL receive `403`.

#### Scenario: Env-bootstrapped admin
- **WHEN** a user registers with an email listed in `FD_ADMIN_EMAILS`
- **THEN** their account is created with `is_admin = true`

#### Scenario: Non-admin rejected from group API
- **WHEN** a non-admin user calls `POST /api/v1/groups`
- **THEN** the system responds `403`

#### Scenario: Admin reassigns a user's group
- **WHEN** an admin posts a user's id to a different group
- **THEN** subsequent limit checks for that user use the new group's config

### Requirement: Per-user configuration via one-person groups

Per-user customization SHALL be achieved by creating a one-person group and assigning the user to it; the system SHALL NOT maintain a parallel per-user override layer.

#### Scenario: Special-case one user
- **WHEN** an admin creates group `vip-alice` with raised limits and assigns user alice to it
- **THEN** alice's limit checks use `vip-alice`'s config while other users keep their groups' configs
