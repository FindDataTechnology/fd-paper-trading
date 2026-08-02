# web-order-entry Specification

## Purpose
TBD - created by syncing change trading-web-ui. Update Purpose after implementation.

## Requirements

### Requirement: Order ticket
The system SHALL provide an order ticket that lets a user place a market or limit, buy or sell order for a selected symbol on a selected account, consuming `POST /api/v1/orders`.

#### Scenario: Place a market order
- **WHEN** a user selects an account, symbol, side BUY, order type MARKET, and a quantity, then submits
- **THEN** the SPA POSTs the order to `/api/v1/orders` and on success shows confirmation and refreshes the orders list

#### Scenario: Place a limit order
- **WHEN** a user selects order type LIMIT and enters a limit price with a quantity
- **THEN** the SPA submits the limit order to `/api/v1/orders` with the limit price

#### Scenario: Quantity must be positive
- **WHEN** a user enters a zero or negative quantity
- **THEN** the SPA disables the submit button and shows a validation error without calling the API

#### Scenario: Limit order requires a price
- **WHEN** a user selects order type LIMIT but leaves the limit price empty
- **THEN** the SPA disables the submit button and shows a validation error

#### Scenario: Buy-side buying power guard
- **WHEN** a user enters a BUY quantity whose estimated cost (quantity × current ask) exceeds the account's available cash
- **THEN** the SPA warns the user before submission and requires confirmation

#### Scenario: API rejection is surfaced
- **WHEN** the API rejects the order (e.g. insufficient funds, invalid symbol)
- **THEN** the SPA displays the rejection reason returned by the API and does not add the order to the list

### Requirement: Orders list
The system SHALL provide an orders list for the selected account that displays each order's symbol, side, type, quantity, filled quantity, status, and time, consuming `GET /api/v1/accounts/{id}/orders`.

#### Scenario: List orders for an account
- **WHEN** a user views the orders view for an account
- **THEN** the SPA fetches and displays all orders for that account

#### Scenario: Filter by status
- **WHEN** a user selects a status filter (e.g. OPEN, FILLED, CANCELLED)
- **THEN** the SPA filters the displayed orders to that status

#### Scenario: Empty state
- **WHEN** an account has no orders
- **THEN** the SPA shows an empty-state message instead of an empty table

### Requirement: Order detail and cancellation
The system SHALL provide an order detail view showing fills and a cancel action for open orders, consuming `GET /api/v1/orders/{id}` and `DELETE /api/v1/orders/{id}`.

#### Scenario: View order detail with fills
- **WHEN** a user opens an order
- **THEN** the SPA displays the order's status, quantity, avg fill price, and the list of fills

#### Scenario: Cancel an open order
- **WHEN** a user cancels an open order and confirms
- **THEN** the SPA calls `DELETE /api/v1/orders/{id}` and updates the order's status to CANCELLED in the list and detail view

#### Scenario: Cancel is only offered for open orders
- **WHEN** an order is already FILLED or CANCELLED
- **THEN** the SPA does not offer a cancel action for that order

### Requirement: Live order updates
The system SHALL reflect order-status and fill events pushed over the WebSocket by updating the affected order in the list and detail view without a full refetch.

#### Scenario: Fill arrives over WebSocket
- **WHEN** a fill event for an open order arrives over `/ws`
- **THEN** the SPA updates that order's filled quantity, avg fill price, and status in place
