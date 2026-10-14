## RENAMED Requirements

- FROM: `### Requirement: Stateless Nautilus fill calculator`
- TO: `### Requirement: Stateless fill calculator`

## MODIFIED Requirements

### Requirement: Stateless fill calculator

The system SHALL compute order fills with a stateless, pure-arithmetic fill calculator
that takes one quote and one order and returns the fill outcome, with no third-party
matching engine and no per-order engine construction. Account state (positions, balances)
SHALL NOT live in the calculator; it SHALL live in the database, so accounts are isolated
by `account_id`. The calculator SHALL run off the FastAPI event loop (offloaded) so the API
is not blocked.

Fill semantics SHALL be: a market buy fills at the `ask`; a market sell fills at the `bid`;
a limit buy fills at the `ask` when its limit is at or above the `ask`, otherwise it rests;
a limit sell fills at the `bid` when its limit is at or below the `bid`, otherwise it rests.
The calculator SHALL NOT model queueing, partial fills, slippage, or price movement.

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
