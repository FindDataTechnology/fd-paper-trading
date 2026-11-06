## MODIFIED Requirements

### Requirement: Stateless fill calculator

The system SHALL compute order fills with a stateless, pure-arithmetic fill calculator
that takes one quote and one order (and, for stop-type orders, the order's trigger state)
and returns the fill outcome, with no third-party matching engine and no per-order engine
construction. Account state (positions, balances) SHALL NOT live in the calculator; it
SHALL live in the database, so accounts are isolated by `account_id`. The calculator SHALL
run off the FastAPI event loop (offloaded) so the API is not blocked.

Fill semantics SHALL be: a market buy fills at the `ask`; a market sell fills at the `bid`;
a limit buy fills at the `ask` when its limit is at or above the `ask`, otherwise it rests;
a limit sell fills at the `bid` when its limit is at or below the `bid`, otherwise it rests;
a stop sell triggers when the quote's `bid` is at or below its `stop_price` and fills at the
`stop_price`; a stop buy triggers when the quote's `ask` is at or above its `stop_price` and
fills at the `stop_price`; a stop-limit order first applies the stop trigger rule, and once
triggered follows the corresponding limit rule. The calculator SHALL NOT model queueing,
partial fills, slippage, or price movement.

#### Scenario: Calculator computes a fill price

- **WHEN** a market buy for a symbol with a current quote is placed
- **THEN** the calculator returns the fill price (the ask) without touching account balances

#### Scenario: No third-party matching engine is required

- **WHEN** an order is filled
- **THEN** the fill is computed in-process by the service's own calculator, with no
  third-party trading framework imported or invoked

#### Scenario: Resting limit consumes no state

- **WHEN** a limit order does not cross the quote
- **THEN** the calculator reports a resting outcome with zero filled quantity and creates
  no position

#### Scenario: Stop sell triggers at the stop price

- **WHEN** a sell stop with `stop_price = s` is evaluated against a quote whose `bid ≤ s`
- **THEN** the fill price is `s`, computed without touching account balances

#### Scenario: Stop not yet triggered stays resting

- **WHEN** a sell stop with `stop_price = s` is evaluated against a quote whose `bid > s`
- **THEN** the calculator reports a not-triggered outcome with zero filled quantity

#### Scenario: Stop-limit falls back to the limit rule after trigger

- **WHEN** a sell stop-limit with `stop_price = s` and `limit_price = l ≤ s` is evaluated
  against a quote whose `bid ≤ s`
- **THEN** the trigger condition is met and the subsequent fill follows the sell-limit rule
  against the same quote (fills at `bid` when `bid ≤ l`, otherwise rests as triggered-limit)

## ADDED Requirements

### Requirement: Stop orders rest, trigger, and cancel like other orders

The service SHALL accept `STOP` and `STOP_LIMIT` orders with a required `stop_price`
(and `limit_price` for STOP_LIMIT). An untriggered stop order SHALL rest with status
`OPEN`, create no position, and hold no cash. Trigger evaluation SHALL happen when a new
quote for the symbol arrives (live tick, manual fill attempt, or run tick); on trigger, the
order follows its fill rule immediately and SHALL be recorded as `TRIGGERED` (STOP, then
FILLED in the same step) or `OPEN` (STOP_LIMIT resting as a triggered limit). Untriggered
stop orders SHALL be cancellable via the existing cancellation path. Validation SHALL
reject a stop order whose `stop_price`/`limit_price` combination cannot trigger sensibly
(a sell stop-limit with `limit_price > stop_price` is rejected).

#### Scenario: Sell stop triggers on a new quote

- **WHEN** an account holds a position and a resting sell stop, and the latest close drops
  to or below the stop price
- **THEN** the next quote arrival triggers the order, which fills at its stop price with an
  immutable `Fill` row, and the position decreases

#### Scenario: Untriggered stop is cancellable

- **WHEN** a resting stop order is cancelled before its trigger
- **THEN** it becomes `CANCELLED`, no fill is recorded, and no position changed

#### Scenario: Nonsensical stop-limit combination is rejected

- **WHEN** a sell stop-limit is submitted with `limit_price > stop_price`
- **THEN** the order is rejected with a validation error naming the fields
