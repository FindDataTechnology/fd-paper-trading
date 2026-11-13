## MODIFIED Requirements

### Requirement: Provider is swappable

The market-data layer SHALL expose a single interface so the simulated, Polygon, fd-open-data, and astock providers are interchangeable, and a real-time provider (e.g. Databento NBBO) can be added later without changing consumers. Selection SHALL be via `MARKET_DATA_SOURCE_ID`, a source id registered in the price-source registry: the declared `access.kind` decides the provider class (`federated_view` → the astock/federated provider, `fd_open_data_ontology` → the fd-open-data provider, `polygon` → the Polygon provider, `simulated` → the simulated provider). An unset source id SHALL select the simulated provider. An id that is not registered SHALL fail startup with the id named — never a silent fallback to another provider. The legacy selectors `MARKET_DATA_PROVIDER` / `ASTOCK_SOURCE` / `ASTOCK_CALENDAR_REFS` were removed; a run that still has them set SHALL fail startup naming them, because "I changed it" and "it never took effect" must not look the same.

#### Scenario: Selecting the provider at startup
- **WHEN** `MARKET_DATA_SOURCE_ID` is unset
- **THEN** the app uses `SimulatedMarketDataProvider`
- **AND WHEN** `MARKET_DATA_SOURCE_ID=astock_daily` (registered, `access.kind=federated_view`, read-only DSN configured)
- **THEN** the app uses `AstockMarketDataProvider` with `db_source=astock_daily`
- **AND WHEN** `MARKET_DATA_SOURCE_ID` names an id the registry does not carry
- **THEN** startup fails with that id named, rather than falling through to another provider
- **AND WHEN** a legacy variable (`MARKET_DATA_PROVIDER` / `ASTOCK_SOURCE` / `ASTOCK_CALENDAR_REFS`) is still present in the environment
- **THEN** startup fails naming the offending variable(s)
