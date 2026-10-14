# strategy-management Specification

## Purpose

约束策略实体与版本化：spec 双轨（模板/自定义代码）形态校验、版本号随 spec 变更递增、命名与描述编辑不触发版本。
## Requirements
### Requirement: Strategy entity with versioning

The system SHALL persist strategies owned by a user. A strategy SHALL carry `id`, `user_id`, `name`, `description`, a `spec` (JSON), a monotonically increasing `version` (starting at 1), and timestamps. Any update that changes `spec` SHALL increment `version`; updates to `name`/`description` alone SHALL NOT. Users SHALL only see and modify their own strategies.

#### Scenario: Create strategy starts at version 1
- **WHEN** a user creates a strategy with a valid spec
- **THEN** the strategy is persisted with `version = 1`

#### Scenario: Spec update bumps version
- **WHEN** a user updates a strategy's spec
- **THEN** `version` increments by 1 and the new spec replaces the old

#### Scenario: Metadata-only update keeps version
- **WHEN** a user updates only the strategy's name or description
- **THEN** `version` is unchanged

#### Scenario: User isolation
- **WHEN** a user requests a strategy owned by another user
- **THEN** the system responds `404`

### Requirement: Dual-track strategy spec

A strategy `spec` SHALL be exactly one of two kinds. Template track: `{"kind": "template", "template": <template-id>, "symbol": <symbol>, "params": {...}}`. Custom-code track: `{"kind": "code", "symbol": <symbol>, "source": <python source>}`. A spec with any other `kind`, missing required keys, or an empty `symbol` SHALL be rejected with a structured validation error listing every violation.

#### Scenario: Unknown kind rejected
- **WHEN** a strategy is created with `kind` other than `template` or `code`
- **THEN** the system responds `400` with an error enumerating the violation

#### Scenario: Empty symbol rejected
- **WHEN** a strategy spec has a missing or blank `symbol`
- **THEN** the system responds `400` with a validation error

### Requirement: Built-in template catalog

The system SHALL provide a built-in template catalog of at least: `sma_cross` (fast/slow periods), `rsi_reversion` (period, oversold, overbought), `bollinger_breakout` (period, std multiplier), `macd_cross` (fast/slow/signal), and `dca` (interval in bars). Each template SHALL declare a JSON Schema for its parameters including defaults and a required fixed `quantity`. The catalog SHALL be queryable via API so UIs and LLM tools can render/fill parameters without hardcoding.

#### Scenario: Catalog lists templates with schemas
- **WHEN** a client requests the template catalog
- **THEN** each entry includes an id, a human description, and a JSON Schema for its params

#### Scenario: Template params validated against schema
- **WHEN** a strategy is created with `template: "sma_cross"` and `params: {"fast": -3}`
- **THEN** the system responds `400` identifying the invalid parameter

#### Scenario: Unknown template rejected
- **WHEN** a strategy references a template id not in the catalog
- **THEN** the system responds `400` listing the valid template ids

### Requirement: Template track available to all users

The template track SHALL be available to every user regardless of group, subject only to symbol-allowlist limits. Template strategies SHALL execute as interpreted parameter logic - never as generated code.

#### Scenario: User in default group creates template strategy
- **WHEN** a user whose group has `allow_custom_code: false` creates a template-track strategy
- **THEN** the strategy is accepted

### Requirement: Strategy REST API

The system SHALL expose authenticated endpoints: `POST /api/v1/strategies` (create), `GET /api/v1/strategies` (list own), `GET /api/v1/strategies/{id}` (detail), `PATCH /api/v1/strategies/{id}` (update; spec changes bump version), `DELETE /api/v1/strategies/{id}`. Deleting a strategy with an active paper run SHALL be rejected until the run is stopped.

#### Scenario: Delete blocked while running
- **WHEN** a user deletes a strategy that has a paper run in `RUNNING` status
- **THEN** the system responds `409` and the strategy is not deleted

#### Scenario: CRUD round trip
- **WHEN** a user creates, reads, updates, and lists a strategy
- **THEN** each operation reflects the prior mutations with correct versioning

### Requirement: Strategy web pages

The web UI SHALL provide a strategies list page (name, spec summary, active run status, latest version) and a strategy detail page. The detail page SHALL render template-track specs as an editable parameter form and code-track specs as a text editor; saving a spec change SHALL show the new version number. All strategy pages SHALL require authentication.

#### Scenario: Template strategy renders as form
- **WHEN** a user opens a template-track strategy in the web UI
- **THEN** the page shows labeled inputs for each template parameter with current values

#### Scenario: Code strategy renders as editor
- **WHEN** a user opens a code-track strategy in the web UI
- **THEN** the page shows the source in an editable text area

#### Scenario: Unauthenticated access redirected
- **WHEN** an unauthenticated user navigates to a strategy page
- **THEN** they are redirected to the login page
