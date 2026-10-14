# market-data Specification

## Purpose

约束行情供给抽象：provider 接口、三实现（模拟/Polygon/fd-open-data）的语义与数据缺口表达（None=gap）、概念族可配置，以及回测/实盘/撮合共用同一价格世界。
## Requirements
### Requirement: MarketDataProvider interface

The system SHALL define a `MarketDataProvider` abstraction with these operations: `get_quote(symbol) -> Quote | None` (async), `get_instrument(symbol) -> Instrument | None`, `search_instruments(query)`, and `get_bars(symbol, timeframe, limit)`. A `Quote` SHALL carry `instrument_id`, `bid`, `ask`, and `ts_event`. `get_quote` returning `None` SHALL signify a data gap (no live quote available). The provider is selected at app startup from `MARKET_DATA_PROVIDER` (`simulated` | `polygon` | `fd-open-data`).

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

The market-data layer SHALL expose a single interface so the simulated, Polygon, and fd-open-data providers are interchangeable, and a real-time provider (e.g. Databento NBBO) can be added later without changing consumers. Selection SHALL be via the `MARKET_DATA_PROVIDER` environment variable.

#### Scenario: Selecting the provider at startup
- **WHEN** `MARKET_DATA_PROVIDER=polygon` (with a key)
- **THEN** the app uses `PolygonMarketDataProvider`
- **AND WHEN** `MARKET_DATA_PROVIDER=fd-open-data`
- **THEN** the app uses `FdOpenDataMarketDataProvider`
- **AND WHEN** `MARKET_DATA_PROVIDER=simulated` (or unset/unknown)
- **THEN** the app uses `SimulatedMarketDataProvider`

### Requirement: fd-open-data provider (real daily bars and latest-close quotes)

The `FdOpenDataMarketDataProvider` SHALL be backed by the `fd-open-data-mcp` package imported as a library (in-process; no MCP client). `get_bars(symbol, "1d", limit)` SHALL return real historical daily OHLCV bars loaded through a bulk range read (`read_range`) - one upstream range call per concept, not one dispatch per date. `get_quote(symbol)` SHALL derive a quote from the latest available daily close with a synthetic half-spread (5 bps each side, `$0.01` floor - the same derivation as the Polygon provider), returning `None` when no close is available (data gap). `get_instrument` SHALL keep the SIM equity universe; `search_instruments` SHALL resolve entities through `fd-open-data-mcp`'s entity identifiers with fallback to the curated universe. Timeframes other than `1d` are unsupported by this provider.

#### Scenario: Real daily bars served
- **WHEN** `get_bars("AAPL", "1d", 500)` is called and the fd-open-data store has that range cached/fetchable
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

