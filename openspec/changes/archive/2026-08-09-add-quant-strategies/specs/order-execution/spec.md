# order-execution Specification (delta)

## ADDED Requirements

### Requirement: Strategy order attribution

Orders SHALL carry an optional `strategy_run_id` (nullable FK to `strategy_runs`, indexed). Orders placed by a live paper run SHALL have it set; manual orders SHALL have it null. The orders API SHALL expose `strategy_run_id` in order responses and SHALL support filtering an account's orders by it, so a run's order history is queryable. Attribution SHALL NOT change any execution semantics (fills, buying-power checks, cancellation are identical for attributed and manual orders).

#### Scenario: Strategy order is attributed
- **WHEN** a live paper run places an order
- **THEN** the order record and its API response include that run's `strategy_run_id`

#### Scenario: Manual order has no attribution
- **WHEN** a user places an order directly
- **THEN** the order's `strategy_run_id` is null

#### Scenario: Filter orders by run
- **WHEN** a client requests an account's orders filtered by a `strategy_run_id`
- **THEN** exactly the orders placed by that run are returned

#### Scenario: Execution semantics unchanged
- **WHEN** two identical orders are placed, one manual and one strategy-attributed
- **THEN** both receive the same fill price and ledger treatment
