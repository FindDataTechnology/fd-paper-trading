## MODIFIED Requirements

### Requirement: Stop orders rest, trigger, and cancel like other orders

The service SHALL accept `STOP` and `STOP_LIMIT` orders with a required `stop_price`
(and `limit_price` for STOP_LIMIT). An untriggered stop order SHALL rest with status
`OPEN`, create no position, and hold no cash. Trigger evaluation SHALL happen when a new
quote for the symbol arrives (live tick, manual fill attempt, run tick, or a periodic
sweep); on trigger, the order follows its fill rule immediately and SHALL be recorded as
`TRIGGERED` (STOP, then FILLED in the same step) or `OPEN` (STOP_LIMIT resting as a
triggered limit). The trigger moment SHALL be recorded only when the quote actually
crosses the stop price (buy: `ask >= stop_price`; sell: `bid <= stop_price`) — never
inferred from a resting status, because "resting" and "triggered but resting" are
different states that share one status value; a stop whose trigger condition never held
SHALL NOT fill at any number of evaluations. Untriggered stop orders SHALL be cancellable
via the existing cancellation path. Validation SHALL reject a stop order whose
`stop_price`/`limit_price` combination cannot trigger sensibly (a sell stop-limit with
`limit_price > stop_price` is rejected).

#### Scenario: Sell stop triggers on a new quote

- **WHEN** an account holds a position and a resting sell stop, and the latest close drops
  to or below the stop price
- **THEN** the next quote arrival triggers the order, which fills at its stop price with an
  immutable `Fill` row, and the position decreases

#### Scenario: An untouched stop never fills

- **WHEN** a resting sell stop's stop price is below every quote seen since it was placed
- **THEN** no evaluation of that account's stops fills it, and its trigger moment is not recorded, however many times it is evaluated

#### Scenario: Trigger state agrees with the fill decision

- **WHEN** a stop order is evaluated against a quote
- **THEN** the order's recorded trigger moment is set if and only if the quote crossed its stop price (or the stop had already triggered), so a `STOP_LIMIT` resting as a triggered limit keeps that state on later evaluations

#### Scenario: Untriggered stop is cancellable

- **WHEN** a resting stop order is cancelled before its trigger
- **THEN** it becomes `CANCELLED`, no fill is recorded, and no position changed

#### Scenario: Nonsensical stop-limit combination is rejected

- **WHEN** a sell stop-limit is submitted with `limit_price > stop_price`
- **THEN** the order is rejected with a validation error naming the fields

## ADDED Requirements

### Requirement: Resting stops are re-evaluated without user action

The service SHALL re-evaluate resting `STOP`/`STOP_LIMIT` orders on its own, so a stop on an account that no user action touches still triggers when the price crosses. The evaluation SHALL run on a configurable cadence (`STOP_SWEEP_SECONDS`, default 60; `0` disables it) and SHALL go through the same trigger/fill path as a quote arriving from an order placement, so no fill, buying-power or staleness rule is duplicated. A sweep SHALL NOT fabricate prices: it reads the market-data provider's real quote, not the realtime stream's display wobble. A sweep SHALL NOT take over the stops of an account that has an active strategy run, because that run evaluates them itself and records the trigger in the run's event log.

#### Scenario: A manual account's stop triggers on its own

- **WHEN** an account with no strategy run holds a resting sell stop and the close falls to or below the stop price
- **THEN** a later sweep triggers it without any further request from the owner, and the resulting order event reaches the owner over `/ws`

#### Scenario: A sweep does not steal a run's stops

- **WHEN** the account holding a resting stop has an active strategy run
- **THEN** the sweep skips that account, leaving the trigger and its run-log entry to the run's own evaluation

#### Scenario: Zero disables the sweep

- **WHEN** `STOP_SWEEP_SECONDS` is `0`
- **THEN** no sweep task runs and resting stops are only evaluated when a quote arrives from another path
