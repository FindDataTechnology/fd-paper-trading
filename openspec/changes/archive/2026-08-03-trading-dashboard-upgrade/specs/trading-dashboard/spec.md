## ADDED Requirements

### Requirement: Trading dashboard with watchlist and order panel

The system SHALL provide a trading dashboard that displays three main panels: a watchlist (left) showing live quotes for tracked symbols, a price chart (center) for the selected symbol, and an order panel (right) that is contextually bound to the currently selected symbol. The watchlist SHALL update in real-time via WebSocket as new quote data arrives. Clicking any symbol in the watchlist SHALL set it as the active symbol for both the chart and the order panel without requiring navigation.

#### Scenario: Watchlist displays live quotes
- **WHEN** user opens the trading dashboard
- **THEN** the watchlist displays a configurable list of symbols with their current bid/ask prices updated in real-time

#### Scenario: Click-to-select symbol updates order panel
- **WHEN** user clicks a symbol in the watchlist
- **THEN** the order panel instantly shows that symbol's name pre-filled and ready for order entry

#### Scenario: Order panel is always accessible
- **WHEN** user is on the dashboard
- **THEN** the order panel is visible at all times and can accept orders immediately

### Requirement: Price chart integration

The system SHALL display an interactive price chart for the selected symbol in the center panel. The chart SHALL update in real-time with incoming quote ticks. Chart timeframe controls SHALL be available for historical view.

#### Scenario: Chart updates on symbol selection
- **WHEN** user clicks a different symbol in the watchlist
- **THEN** the center chart immediately switches to display that symbol's data

#### Scenario: Real-time chart updates
- **WHEN** new quote data arrives via WebSocket
- **THEN** the chart updates to reflect the latest price without page refresh

### Requirement: Dashboard layout configuration

The system SHALL provide a responsive layout that works on desktop screens (minimum 1200px width). On smaller screens, the layout SHALL collapse to maintain usability with stacked panels or collapsible sections.

#### Scenario: Desktop layout renders correctly
- **WHEN** user accesses dashboard on screen >= 1200px
- **THEN** the layout displays three columns: watchlist (left), chart (center), order panel (right)

#### Scenario: Mobile layout collapses gracefully
- **WHEN** user accesses dashboard on screen < 1200px
- **THEN** the layout stacks panels vertically or provides collapsible sections
