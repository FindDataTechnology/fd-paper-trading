# Position Actions

**Purpose**: Provide user-friendly controls for closing and reducing positions with appropriate safety mechanisms.

## Requirements

### Requirement: Position close action

The system SHALL provide a "Close" action button on each position row in the positions table. Clicking "Close" SHALL submit a market order in the opposite direction of the current position (sell for long, buy for short) to fully close the position. A confirmation dialog SHALL appear before submission.

#### Scenario: Close long position
- **WHEN** user clicks "Close" on a long position of 100 shares of AAPL
- **THEN** a market SELL order for 100 shares is submitted and the confirmation dialog appears first

#### Scenario: Close short position
- **WHEN** user clicks "Close" on a short position of 50 shares of TSLA
- **THEN** a market BUY order for 50 shares is submitted and the confirmation dialog appears first

### Requirement: Position reduce action

The system SHALL provide a "Reduce" action on each position row. Clicking "Reduce" SHALL open an inline input allowing the user to specify a quantity to reduce by. Submitting SHALL place a market order in the opposite direction for the specified quantity (must be less than or equal to the full position size).

#### Scenario: Reduce position by partial amount
- **WHEN** user clicks "Reduce" on a 100-share position and enters 30
- **THEN** a market order for 30 shares in the opposite direction is submitted

#### Scenario: Reject reduce quantity exceeding position size
- **WHEN** user enters a reduce quantity greater than the current position
- **THEN** the system shows a validation error and does not submit the order

### Requirement: Position actions require confirmation

All position close and reduce actions SHALL require explicit user confirmation before order submission. The confirmation dialog SHALL display the symbol, side, quantity, and order type (market).

#### Scenario: Confirmation dialog shows order details
- **WHEN** user initiates a close or reduce action
- **THEN** a confirmation dialog displays symbol, side (BUY/SELL), quantity, and "Market" order type before proceeding

#### Scenario: Cancel confirmation aborts action
- **WHEN** user cancels the confirmation dialog
- **THEN** no order is submitted and the position remains unchanged
