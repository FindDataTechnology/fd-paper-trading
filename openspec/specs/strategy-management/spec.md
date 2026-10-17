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

A strategy `spec` SHALL be exactly one of three kinds. Template track: `{"kind": "template", "template": <template-id>, "symbol": <symbol>, "params": {...}}`. Custom-code track: `{"kind": "code", "symbol": <symbol>, "source": <python source>}`. Weight track (multi-symbol): `{"kind": "weight", "universe": [<symbol>...], "rebalance": {"every_n_bars": <positive int>}}` plus exactly one of `"source": <python source>` (defining `target_weights(ctx)`) or `"weights_series": {<date>: {<symbol>: <number>}}`. A spec with any other `kind`, missing required keys, an empty `symbol`, an empty or duplicate-containing `universe`, a non-positive `every_n_bars`, both-or-neither of `source`/`weights_series`, or any weight that is negative or whose sum exceeds 1 SHALL be rejected with a structured validation error listing every violation. Validation SHALL NOT normalize or clip weights.

#### Scenario: Unknown kind rejected
- **WHEN** a strategy is created with `kind` other than `template`, `code`, or `weight`
- **THEN** the system responds `400` with an error enumerating the violation

#### Scenario: Empty symbol rejected
- **WHEN** a strategy spec has a missing or blank `symbol`
- **THEN** the system responds `400` with a validation error

#### Scenario: Weight spec requires universe and one source
- **WHEN** a weight spec omits `universe`, or provides both `source` and `weights_series`, or provides neither
- **THEN** the system responds `400` enumerating every violation

#### Scenario: Illegal weights rejected without normalization
- **WHEN** a weight spec's series contains a period whose weights sum to more than 1
- **THEN** the system responds `400` naming that date and sum, and stores no strategy

### Requirement: Built-in template catalog

The system SHALL provide a built-in template catalog of at least: `sma_cross` (fast/slow periods), `rsi_reversion` (period, oversold, overbought), `bollinger_breakout` (period, std multiplier), `macd_cross` (fast/slow/signal), and `dca` (interval in bars). Each template SHALL declare a JSON Schema for its parameters including defaults and a required fixed `quantity`. The catalog SHALL be queryable via API so UIs and LLM tools can render/fill parameters without hardcoding. The catalog SHALL additionally expose the weight track's parameter surface (universe size limits, rebalance interval, and the two source forms) as machine-readable metadata so UIs and tools can author weight strategies without hardcoding the shape.

#### Scenario: Catalog lists templates with schemas
- **WHEN** a client requests the template catalog
- **THEN** each entry includes an id, a human description, and a JSON Schema for its params

#### Scenario: Template params validated against schema
- **WHEN** a strategy is created with `template: "sma_cross"` and `params: {"fast": -3}`
- **THEN** the system responds `400` identifying the invalid parameter

#### Scenario: Unknown template rejected
- **WHEN** a strategy references a template id not in the catalog
- **THEN** the system responds `400` listing the valid template ids

#### Scenario: Weight track surface is queryable
- **WHEN** a client requests the catalog
- **THEN** the response includes the weight track's authoring metadata (universe limit, rebalance field, source forms)

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

The web UI SHALL provide a strategies list page (name, spec summary, active run status, latest version) and a strategy detail page. The detail page SHALL render template-track specs as an editable parameter form, code-track specs as a text editor, and weight-track specs with a universe editor (symbol list), a rebalance interval input, and a source editor (or a read-only weights-series view for the static form); saving a spec change SHALL show the new version number. All strategy pages SHALL require authentication.

#### Scenario: Template strategy renders as form
- **WHEN** a user opens a template-track strategy in the web UI
- **THEN** the page shows labeled inputs for each template parameter with current values

#### Scenario: Code strategy renders as editor
- **WHEN** a user opens a code-track strategy in the web UI
- **THEN** the page shows the source in an editable text area

#### Scenario: Unauthenticated access redirected
- **WHEN** an unauthenticated user navigates to a strategy page
- **THEN** they are redirected to the login page

#### Scenario: Weight strategy renders its editor
- **WHEN** a user opens a weight-track strategy
- **THEN** the page shows the universe editor, rebalance input, and the source/weights-series editor, and saving bumps the version
