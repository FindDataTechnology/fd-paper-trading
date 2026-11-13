## MODIFIED Requirements

### Requirement: MarketDataProvider interface

The system SHALL define a `MarketDataProvider` abstraction with these operations:
`get_quote(symbol) -> Quote | None` (async), `get_instrument(symbol) -> Instrument | None`,
`search_instruments(query)`, and `get_bars(symbol, timeframe, limit)`. A `Quote` SHALL carry
`instrument_id`, `bid`, `ask`, and `ts_event`. `get_quote` returning `None` SHALL signify a
data gap (no live quote available). The provider is selected at app startup via
`MARKET_DATA_SOURCE_ID` resolved through the price-source registry (see "Provider is
swappable"); this requirement does not carry its own provider enumeration, so adding a source
does not require editing it.

#### Scenario: Quote carries bid and ask

- **WHEN** the provider returns a quote for a symbol
- **THEN** the quote has a `bid`, an `ask`, and an `instrument_id`

#### Scenario: Data gap signaled as None

- **WHEN** no quote is available for a symbol
- **THEN** `get_quote` returns `None`
