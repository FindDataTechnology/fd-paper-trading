# market-data Specification

## Purpose

约束行情供给抽象：provider 接口、三实现（模拟/Polygon/fd-open-data）的语义与数据缺口表达（None=gap）、概念族可配置，以及回测/实盘/撮合共用同一价格世界。

## Requirements

### Requirement: MarketDataProvider interface

The system SHALL define a `MarketDataProvider` abstraction with these operations:
`get_quote(symbol) -> Quote | None` (async), `get_instrument(symbol) -> Instrument | None`,
`search_instruments(query)`, and `get_bars(symbol, timeframe, limit)`. A `Quote` SHALL carry
`instrument_id`, `bid`, `ask`, and `ts_event`. `get_quote` returning `None` SHALL signify a
data gap (no live quote available). The provider is selected at app startup from
`MARKET_DATA_PROVIDER` (`simulated` | `polygon` | `fd-open-data`).

#### Scenario: Quote carries bid and ask

- **WHEN** the provider returns a quote for a symbol
- **THEN** the quote has a `bid`, an `ask`, and an `instrument_id`

#### Scenario: Data gap signaled as None

- **WHEN** no quote is available for a symbol
- **THEN** `get_quote` returns `None`

### Requirement: Simulated provider (deterministic)

The `SimulatedMarketDataProvider` SHALL return a deterministic quote per symbol (derived
from a hash of the symbol) so tests are reproducible, with `bid`/`ask` straddling a base
price. The simulated provider SHALL always return a quote (no data gaps).

#### Scenario: Deterministic quote

- **WHEN** the simulated provider is asked for the same symbol twice
- **THEN** it returns identical `bid`/`ask` values, with `ask > bid`

### Requirement: Polygon provider (real previous-close)

The `PolygonMarketDataProvider` SHALL derive a quote from Polygon's previous-close
aggregate (free tier): the last close is the reference price, and a small spread (5 bps
each side, `$0.01` floor) is synthesized around it to produce `bid`/`ask`. Quotes SHALL be
cached per symbol for a TTL (default 300s). The provider SHALL return `None` when there is
no API key, on HTTP error, on a non-200 response (rate-limited / not entitled / unknown
symbol), or when no close is present. Instruments SHALL come from the service's own
instrument value type.

#### Scenario: No API key returns no quote

- **WHEN** the Polygon provider is configured with an empty API key
- **THEN** `get_quote` returns `None`

#### Scenario: Synthesized spread around previous close

- **WHEN** the Polygon provider fetches a previous close of `c`
- **THEN** the returned `bid < c < ask` and `bid != ask`

### Requirement: Instrument resolution

`get_instrument(symbol)` SHALL return a non-`None` instrument value for a symbol the
provider knows, and `None` for one it does not. The instrument SHALL be the service's own
value type carrying at least the symbol, a display name, the trading venue identifier, and
the price and quantity precision used when quantizing fills — it SHALL NOT be a type
supplied by a third-party trading framework. Consumers SHALL treat a non-`None` instrument
as the precondition for accepting an order for that symbol.

#### Scenario: Known symbol returns an instrument

- **WHEN** the provider is asked for an instrument for a supported symbol
- **THEN** it returns a non-`None` instrument carrying that symbol and a venue

#### Scenario: Unknown symbol returns nothing

- **WHEN** the provider is asked for an instrument for a symbol outside its universe
- **THEN** the outcome is `None` or a rejection that prevents order acceptance

### Requirement: Provider is swappable

The market-data layer SHALL expose a single interface so the simulated, Polygon, and fd-open-data providers are interchangeable, and a real-time provider (e.g. Databento NBBO) can be added later without changing consumers. Selection SHALL be via the `MARKET_DATA_PROVIDER` environment variable.

#### Scenario: Selecting the provider at startup
- **WHEN** `MARKET_DATA_PROVIDER=polygon` (with a key)
- **THEN** the app uses `PolygonMarketDataProvider`
- **AND WHEN** `MARKET_DATA_PROVIDER=fd-open-data`
- **THEN** the app uses `FdOpenDataMarketDataProvider`
- **AND WHEN** `MARKET_DATA_PROVIDER=simulated` (or unset/unknown)
- **THEN** the app uses `SimulatedMarketDataProvider`

### Requirement: fd-open-data provider (real daily bars and latest-close quotes)

The `FdOpenDataMarketDataProvider` SHALL be backed by the `fd-open-data-mcp` package
imported as a library (in-process; no MCP client). `get_bars(symbol, "1d", limit)` SHALL
return real historical daily OHLCV bars loaded through a bulk range read (`read_range`) —
one upstream range call per concept, not one dispatch per date. `get_quote(symbol)` SHALL
derive a quote from the latest available daily close with a synthetic half-spread (5 bps
each side, `$0.01` floor — the same derivation as the Polygon provider), returning `None`
when no close is available (data gap). `get_instrument` SHALL keep the curated universe;
`search_instruments` SHALL resolve entities through `fd-open-data-mcp`'s entity identifiers
with fallback to the curated universe. The provider SHALL resolve the concept family it
reads from configuration. Timeframes other than `1d` are unsupported by this provider.

#### Scenario: Real daily bars served

- **WHEN** `get_bars` is called for a symbol and the fd-open-data store has that range cached or fetchable
- **THEN** it returns real historical daily bars in chronological order (not a synthesized random walk)

#### Scenario: Quote derived from latest close

- **WHEN** the latest daily close for a symbol is `c`
- **THEN** `get_quote` returns `bid < c < ask` with `bid != ask`

#### Scenario: No data is a gap

- **WHEN** the fd-open-data store has no close for a symbol
- **THEN** `get_quote` returns `None`

### Requirement: One price world for backtest, live, and manual fills

When `MARKET_DATA_PROVIDER=fd-open-data`, the same provider instance SHALL feed strategy backtests (historical daily bars), live strategy ticks (latest bar), and order fills (quote). Consumers SHALL NOT mix price series across providers within one app process.

#### Scenario: Backtest and fills share the series
- **WHEN** a strategy is backtested and its signals reference bar closes
- **THEN** manual and live orders on the same symbol fill against quotes derived from that same series' closes

### Requirement: No third-party trading framework at runtime

The market-data layer, the fill calculator, the backtester, and the strategy sandbox SHALL
operate without importing or requiring a third-party trading framework. A clean
installation of the service SHALL NOT pull such a framework into the dependency graph.

#### Scenario: No framework imported by the pricing path

- **WHEN** the market-data layer is imported and exercised
- **THEN** no third-party trading framework is imported

#### Scenario: Clean install omits the framework

- **WHEN** the service is installed from its declared dependencies into an empty environment
- **THEN** no third-party trading framework is present

### Requirement: Concept family is configuration, not code

The concept code, entity type, and frequency the fd-open-data provider reads SHALL be
supplied by configuration, defaulting to a family that is live in the catalog. The
provider SHALL resolve exactly one concept per family and SHALL treat a deprecated concept
as absent, reporting a data gap rather than an error.

#### Scenario: Family selected by configuration

- **WHEN** the provider is configured with a live concept family
- **THEN** it reads that family's concept without code changes

#### Scenario: Deprecated concept is a gap, not a crash

- **WHEN** the configured concept is deprecated in the catalog
- **THEN** the provider yields no data for that symbol and does not raise

#### Scenario: One concept resolved per family

- **WHEN** several concepts match the configured family (for example across sources)
- **THEN** the provider deterministically selects one and reads only from it
