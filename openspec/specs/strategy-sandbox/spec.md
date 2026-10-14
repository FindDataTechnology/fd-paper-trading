# strategy-sandbox Specification

## Purpose
TBD - created by syncing change add-quant-strategies. Update Purpose as needed.
## Requirements
### Requirement: Evaluation contract

Both strategy tracks SHALL evaluate through one contract: `on_bar(ctx)` invoked once per daily bar. `ctx` SHALL expose: bar history accessors `ctx.closes()`, `ctx.opens()`, `ctx.highs()`, `ctx.lows()`, `ctx.volumes()` (oldest-first lists ending at the current bar); indicators `ctx.sma(n)`, `ctx.ema(n)`, `ctx.rsi(n)`, `ctx.macd(fast, slow, signal)`, `ctx.bollinger(n, k)` computed over that history; account state `ctx.position` (signed quantity) and `ctx.cash`; and emitters `ctx.buy(qty, limit_price=None)` and `ctx.sell(qty, limit_price=None)`. Indicators SHALL be implemented once in pure Python and shared by the template interpreter and custom code.

#### Scenario: History is oldest-first ending at current bar
- **WHEN** a strategy calls `ctx.closes()` on the Nth bar of a backtest
- **THEN** it receives exactly N values ending with the current bar's close

#### Scenario: Same indicator values across tracks
- **WHEN** a template and an equivalent code strategy evaluate `sma(10)` on the same bar window
- **THEN** both compute the identical value

### Requirement: AST allowlist validation

Custom strategy source SHALL be validated synchronously at create/update time. Validation SHALL reject (with per-violation messages): any `import` or `from ... import` statement; any name or attribute beginning with `_`; calls to `eval`, `exec`, `open`, `compile`, `globals`, `locals`, `vars`, `__import__`, `input`, or `breakpoint`; and source that does not define a top-level `def on_bar(ctx)`. Source failing validation SHALL NOT be persisted.

#### Scenario: Import rejected
- **WHEN** source contains `import os`
- **THEN** validation fails with a message that imports are forbidden

#### Scenario: Dunder access rejected
- **WHEN** source references `__builtins__` or any attribute starting with `_`
- **THEN** validation fails naming the offending construct

#### Scenario: Missing on_bar rejected
- **WHEN** source defines no top-level `on_bar` function
- **THEN** validation fails stating the required signature

#### Scenario: Valid source accepted
- **WHEN** source defines `def on_bar(ctx)` using only ctx accessors, arithmetic, and comparisons
- **THEN** validation passes and the strategy is persisted

### Requirement: Sandboxed subprocess execution

Custom-code evaluation SHALL execute in a separate worker process (`python -m fd_paper_trading.strategy.sandbox_worker`) receiving the job (source, bar window, position, cash) as JSON on stdin and returning emitted signals as JSON on stdout. The worker SHALL run with resource limits (CPU 10 s, address space 256 MB) and a wall-clock timeout; on timeout, crash, or malformed output the evaluation SHALL be treated as failed. Template-track strategies SHALL NOT use the subprocess (interpreted in-process).

#### Scenario: Infinite loop killed by timeout
- **WHEN** source contains `while True: pass`
- **THEN** the evaluation fails with a timeout error and the host process is unaffected

#### Scenario: Memory bomb contained
- **WHEN** source attempts to allocate an unbounded list
- **THEN** the worker is killed by its address-space limit and the evaluation fails

#### Scenario: Signals returned as JSON
- **WHEN** source calls `ctx.buy(10)` then `ctx.sell(5, limit_price=101.5)`
- **THEN** the worker emits both signals on stdout and exits 0

### Requirement: Custom-code track gated by group

Creating or updating a code-track strategy SHALL be rejected with `403` unless the user's group config has `allow_custom_code: true`. The gate SHALL be independent of validation - valid source from an unauthorized user is still rejected.

#### Scenario: Default group cannot create code strategy
- **WHEN** a user in the default group (`allow_custom_code: false`) creates a code-track strategy
- **THEN** the system responds `403` naming the group restriction

#### Scenario: Enabled group can create code strategy
- **WHEN** a user in a group with `allow_custom_code: true` submits valid source
- **THEN** the strategy is created

### Requirement: Sandbox data only via ctx

The sandbox worker SHALL provide no filesystem, network, or environment access to the strategy beyond the injected `ctx`; strategy source receives data exclusively through ctx accessors. (Limitation: enforcement relies on the no-imports/no-underscore rules plus subprocess resource limits; OS-level network namespaces are out of scope for v1.)

#### Scenario: Source cannot read environment
- **WHEN** source attempts to access process state without imports (e.g. via `().__class__.__base__.__subclasses__()`)
- **THEN** AST validation rejects the `_`-prefixed attribute access before execution
