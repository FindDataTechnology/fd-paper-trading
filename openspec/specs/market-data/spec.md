# market-data Specification

## Purpose
TBD - created by archiving change paper-trading-system. Update Purpose after archive.
## Requirements
### Requirement: MarketDataProvider interface

The system SHALL define a `MarketDataProvider` abstraction with two operations: `get_quote(symbol) -> Quote | None` (async) and `get_instrument(symbol) -> Instrument | None`. A `Quote` SHALL carry `instrument_id`, `bid`, `ask`, and `ts_event`. `get_quote` returning `None` SHALL signify a data gap (no live quote available). The provider is selected at app startup from `MARKET_DATA_PROVIDER` (`simulated` | `polygon`).

#### Scenario: Quote carries bid and ask
- **WHEN** the provider returns a quote for a symbol
- **THEN** the quote has a `bid`, an `ask`, and an `instrument_id`

#### Scenario: Data gap signaled as None
- **WHEN** no quote is available for a symbol
- **THEN** `get_quote` returns `None`

### Requirement: Simulated provider (deterministic)

The `SimulatedMarketDataProvider` SHALL return a deterministic quote per symbol (derived from a hash of the symbol) so tests are reproducible, with `bid`/`ask` straddling a base price. Instruments SHALL be Nautilus `Equity` objects on the `SIM` venue. The simulated provider SHALL always return a quote (no data gaps).

#### Scenario: Deterministic quote
- **WHEN** the simulated provider is asked for the same symbol twice
- **THEN** it returns identical `bid`/`ask` values, with `ask > bid`

### Requirement: Polygon provider (real previous-close)

The `PolygonMarketDataProvider` SHALL derive a quote from Polygon's previous-close aggregate (free tier): the last close is the reference price, and a small spread (5 bps each side, `$0.01` floor) is synthesized around it to produce `bid`/`ask`. Quotes SHALL be cached per symbol for a TTL (default 300s). The provider SHALL return `None` when there is no API key, on HTTP error, on a non-200 response (rate-limited / not entitled / unknown symbol), or when no close is present. Instruments SHALL be `Equity` objects on the `SIM` venue.

#### Scenario: No API key returns no quote
- **WHEN** the Polygon provider is configured with an empty API key
- **THEN** `get_quote` returns `None`

#### Scenario: Synthesized spread around previous close
- **WHEN** the Polygon provider fetches a previous close of `c`
- **THEN** the returned `bid < c < ask` and `bid != ask`

### Requirement: Instrument resolution

`get_instrument(symbol)` SHALL return the Nautilus `Instrument` for a symbol, or `None` if the symbol is unknown to the provider. Instruments back the simulated matching engine; an unknown instrument SHALL cause order placement to be rejected upstream (see `order-execution`).

#### Scenario: Known symbol returns an instrument
- **WHEN** the provider is asked for an instrument for a supported symbol
- **THEN** it returns a non-`None` Nautilus `Instrument`

### Requirement: Provider is swappable

The market-data layer SHALL expose a single interface so the simulated and Polygon providers are interchangeable, and a real-time provider (e.g. Databento NBBO) can be added later without changing consumers. Selection SHALL be via the `MARKET_DATA_PROVIDER` environment variable.

#### Scenario: Selecting the provider at startup
- **WHEN** `MARKET_DATA_PROVIDER=polygon` (with a key)
- **THEN** the app uses `PolygonMarketDataProvider`
- **AND WHEN** `MARKET_DATA_PROVIDER=simulated` (or unset/unknown)
- **THEN** the app uses `SimulatedMarketDataProvider`

