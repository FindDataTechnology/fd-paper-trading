## MODIFIED Requirements

### Requirement: Daily P&L calculation

The system SHALL calculate the daily P&L for the active account **on the server**, from three explicitly stated inputs. The realized component SHALL be the sum of `fills.realized_pnl` for fills whose timestamp falls at or after the day boundary (`pnl_day_start_utc_hour`, default `0` = midnight UTC) and before now — not the lifetime accumulator held on the position row. The unrealized component SHALL be, for each open position, `quantity × (mark − baseline)` where `baseline` is the close of the last daily bar strictly before the current day and `mark` is the current quote mid; a position whose previous close cannot be established SHALL be excluded from the unrealized total rather than valued against its average entry price, and a position without a current quote SHALL be valued at its baseline (contributing zero) with that fact reported. The response SHALL name every such exclusion in a `gaps` list, and SHALL state the day boundary and the baseline kind it used. The calculation SHALL update as new quotes arrive.

#### Scenario: Daily P&L reflects realized gains

- **WHEN** a position is closed with a realized gain of $500 today
- **THEN** the daily P&L displays at least $500 (plus any unrealized P&L)

#### Scenario: Daily P&L updates with live quotes

- **WHEN** new quote data arrives for an open position
- **THEN** the daily P&L recalculates to reflect the updated unrealized P&L

#### Scenario: Lifetime realized gains do not leak into today's figure

- **WHEN** an account realized $900 on a prior day and closes nothing today
- **THEN** the realized component of the daily P&L is zero, although the position row's lifetime `realized_pnl` still reads $900

#### Scenario: An unestablishable baseline is reported, not substituted

- **WHEN** an open position has no daily bar dated before today
- **THEN** that position contributes nothing to the unrealized component
- **AND THEN** the response lists its symbol in `gaps` with reason `no_previous_close`
- **AND THEN** the entry price is never used as the baseline

### Requirement: Daily P&L display on dashboard

The system SHALL display the daily P&L prominently on the trading dashboard, typically in the header or a dedicated summary panel. The display SHALL show the value with appropriate formatting (currency symbol, sign for positive/negative, color coding), and SHALL render the server-provided figures rather than recomputing them from position rows, so that one account shows one number in every client. When the response reports gaps, the display SHALL indicate that the figure is incomplete instead of presenting it as exact.

#### Scenario: P&L displays with color coding

- **WHEN** daily P&L is positive
- **THEN** the value displays in green with a "+" prefix

#### Scenario: P&L displays negative values

- **WHEN** daily P&L is negative
- **THEN** the value displays in red with a "-" prefix

#### Scenario: Gaps make the figure visibly incomplete

- **WHEN** the daily P&L response reports one or more gaps
- **THEN** the dashboard shows the number together with an indication that some positions could not be valued, naming the affected symbols

### Requirement: Daily P&L reset at market open

The system SHALL reset the realized P&L component of the daily P&L calculation at the start of each trading day, where the boundary hour is configurable via `pnl_day_start_utc_hour` (default `0`, i.e. midnight UTC). Positions carried over from previous days SHALL have their unrealized P&L calculated from the previous day's closing price, not the original entry price. Because per-fill realized P&L is recorded from this change onward, fills that predate it carry no realized attribution; the realization window therefore begins at the first fill recorded after the upgrade, and this limitation SHALL be stated by the endpoint rather than left implicit.

#### Scenario: Realized P&L resets at day boundary

- **WHEN** the trading day rolls over (e.g., midnight UTC)
- **THEN** the realized P&L component resets to zero, but unrealized P&L continues to calculate from open positions

#### Scenario: Previous day close price used for carryover positions

- **WHEN** a position is held overnight
- **THEN** the unrealized P&L for that position is calculated from the previous day's closing price, not the original entry price

#### Scenario: The configured boundary is the one applied

- **WHEN** `pnl_day_start_utc_hour` is set to a non-zero hour
- **THEN** the realized window starts at that hour of the current UTC day, and fills between midnight and the boundary belong to the previous trading day
