## MODIFIED Requirements

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
