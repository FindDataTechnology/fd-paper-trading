# web-charts Specification

## Purpose

约束 Web 端图表：TradingView lightweight-charts 的 K 线渲染、实时报价联动与时间框切换。
## Requirements

### Requirement: Historical bars endpoint
The system SHALL expose an authenticated REST endpoint `GET /api/v1/instruments/{symbol}/bars?timeframe=<tf>&limit=<n>` that returns OHLCV bars for the symbol, via a new `get_bars` method on `MarketDataProvider`.

#### Scenario: Fetch bars for a symbol
- **WHEN** an authenticated user requests `/api/v1/instruments/AAPL.SIM/bars?timeframe=1m&limit=200`
- **THEN** the system returns up to 200 one-minute OHLCV bars for that symbol, ordered oldest-first

#### Scenario: Default limit is applied
- **WHEN** no `limit` is provided
- **THEN** the system applies a sensible default limit and returns at most that many bars

#### Scenario: Invalid timeframe is rejected
- **WHEN** the `timeframe` parameter is not a supported value
- **THEN** the system returns `400 Bad Request` with a descriptive error

#### Scenario: Unauthenticated request is rejected
- **WHEN** an unauthenticated request is made to the bars endpoint
- **THEN** the system returns `401 Unauthorized`

### Requirement: Interactive price chart
The system SHALL render an interactive price chart for a selected symbol using TradingView `lightweight-charts`, supporting candlestick and line views and timeframe selection.

#### Scenario: Render candlestick chart
- **WHEN** a user opens a symbol's chart view
- **THEN** the SPA fetches historical bars and renders a candlestick chart

#### Scenario: Switch chart type
- **WHEN** a user toggles between candlestick and line view
- **THEN** the SPA re-renders the chart in the selected type without refetching bars

#### Scenario: Change timeframe
- **WHEN** a user selects a different timeframe (e.g. 5m, 1h, 1d)
- **THEN** the SPA fetches bars for the new timeframe and updates the chart

### Requirement: Live quote overlay and streaming update
The system SHALL overlay the current quote on the chart and append or update the latest bar in real time as quote ticks arrive over the WebSocket.

#### Scenario: Quote overlay is shown
- **WHEN** a chart is displayed and a current quote is available
- **THEN** the SPA shows the latest price, bid, and ask near the chart

#### Scenario: Latest bar updates on tick
- **WHEN** a quote tick arrives over `/ws` for the displayed symbol
- **THEN** the SPA updates the latest bar's close (and high/low as needed) on the chart in place

#### Scenario: Simulated data is labeled
- **WHEN** the data source is the simulated provider
- **THEN** the chart view displays a visible "simulated data" indicator
