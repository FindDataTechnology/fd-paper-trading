## MODIFIED Requirements

### Requirement: Provider is swappable

The market-data layer SHALL expose a single interface so the simulated, Polygon, fd-open-data, and astock providers are interchangeable, and a real-time provider (e.g. Databento NBBO) can be added later without changing consumers. Selection SHALL be via the `MARKET_DATA_PROVIDER` environment variable.

#### Scenario: Selecting the provider at startup
- **WHEN** `MARKET_DATA_PROVIDER=polygon` (with a key)
- **THEN** the app uses `PolygonMarketDataProvider`
- **AND WHEN** `MARKET_DATA_PROVIDER=fd-open-data`
- **THEN** the app uses `FdOpenDataMarketDataProvider`
- **AND WHEN** `MARKET_DATA_PROVIDER=astock` (with a read-only DSN configured)
- **THEN** the app uses `AstockMarketDataProvider`
- **AND WHEN** `MARKET_DATA_PROVIDER=simulated` (or unset/unknown)
- **THEN** the app uses `SimulatedMarketDataProvider`
