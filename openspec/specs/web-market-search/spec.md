# web-market-search Specification

## Purpose

约束 Web 端标的搜索：typeahead 交互、大小写不敏感匹配与空查询短路。
## Requirements

### Requirement: Symbol search endpoint
The system SHALL expose an authenticated REST endpoint `GET /api/v1/instruments/search?q=<query>` that returns instruments whose symbol or name matches the query, via a new `search_instruments` method on `MarketDataProvider`.

#### Scenario: Search by symbol prefix
- **WHEN** an authenticated user requests `/api/v1/instruments/search?q=AAP`
- **THEN** the system returns a list of instruments whose symbol or name matches `AAP`, each with `symbol`, `name`, and `venue`

#### Scenario: Empty query returns no results
- **WHEN** the `q` parameter is empty or whitespace
- **THEN** the system returns an empty list and performs no expensive lookup

#### Scenario: Unknown query returns empty
- **WHEN** the query matches no instrument
- **THEN** the system returns an empty list

#### Scenario: Unauthenticated request is rejected
- **WHEN** an unauthenticated request is made to the search endpoint
- **THEN** the system returns `401 Unauthorized`

### Requirement: Typeahead search UI
The system SHALL provide a typeahead stock search box that queries the search endpoint as the user types and shows matching instruments with a live quote preview.

#### Scenario: Suggestions appear while typing
- **WHEN** a user types at least two characters into the search box
- **THEN** the SPA queries `/api/v1/instruments/search` and displays matching instruments

#### Scenario: Selecting a result opens the instrument
- **WHEN** a user selects a search result
- **THEN** the SPA navigates to that instrument's chart/quote view

#### Scenario: Quote preview in results
- **WHEN** search results are displayed
- **THEN** each result shows the current bid/ask quote for that symbol when available

#### Scenario: No results message
- **WHEN** the search returns no matches
- **THEN** the SPA shows a "no instruments found" message

#### Scenario: Debounced requests
- **WHEN** a user types rapidly
- **THEN** the SPA debounces search requests so it does not send more than one request per debounce window
