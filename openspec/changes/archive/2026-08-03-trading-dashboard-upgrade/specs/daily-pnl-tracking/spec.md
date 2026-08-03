## ADDED Requirements

### Requirement: Daily P&L calculation

The system SHALL calculate and display the total daily P&L for the active account. Daily P&L is defined as the sum of realized P&L from fills that occurred today plus the unrealized P&L from all open positions marked to current market prices. The calculation SHALL update in real-time as new quotes arrive.

#### Scenario: Daily P&L reflects realized gains
- **WHEN** a position is closed with a realized gain of $500 today
- **THEN** the daily P&L displays at least $500 (plus any unrealized P&L)

#### Scenario: Daily P&L updates with live quotes
- **WHEN** new quote data arrives for an open position
- **THEN** the daily P&L recalculates to reflect the updated unrealized P&L

### Requirement: Daily P&L display on dashboard

The system SHALL display the daily P&L prominently on the trading dashboard, typically in the header or a dedicated summary panel. The display SHALL show the value with appropriate formatting (currency symbol, sign for positive/negative, color coding).

#### Scenario: P&L displays with color coding
- **WHEN** daily P&L is positive
- **THEN** the value displays in green with a "+" prefix

#### Scenario: P&L displays negative values
- **WHEN** daily P&L is negative
- **THEN** the value displays in red with a "-" prefix

### Requirement: Daily P&L reset at market open

The system SHALL reset the realized P&L component of the daily P&L calculation at the start of each trading day (configurable, default 00:00 UTC). Positions carried over from previous days SHALL have their unrealized P&L calculated from the previous day's closing price.

#### Scenario: Realized P&L resets at day boundary
- **WHEN** the trading day rolls over (e.g., midnight UTC)
- **THEN** the realized P&L component resets to zero, but unrealized P&L continues to calculate from open positions

#### Scenario: Previous day close price used for carryover positions
- **WHEN** a position is held overnight
- **THEN** the unrealized P&L for that position is calculated from the previous day's closing price, not the original entry price
