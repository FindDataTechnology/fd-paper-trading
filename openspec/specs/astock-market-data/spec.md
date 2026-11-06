# astock-market-data Specification

## Purpose
TBD - created by archiving change astock-price-channel. Update Purpose after archive.

## Requirements

### Requirement: Astock provider reads the sharded daily table through a pinned federated view

`AstockMarketDataProvider` SHALL serve real A-share daily OHLC bars by reading the
platform's federated view over the sharded store, with the read pinned to
`source = 'astock_daily'` (the 前复权 family). The pin SHALL be unconditional: the provider
SHALL NOT mix rows from other sources in the same (concept, entity, date) resolution, and
SHALL treat an ambiguous multi-source row as a defect to reject, not to average. The
provider is read-only: it SHALL NOT write to any store.

#### Scenario: Bars come from the pinned source only

- **WHEN** a symbol's window is read and the federated view holds rows from more than one source
- **THEN** every returned bar originates from `source = 'astock_daily'` rows, and no other source contributes to the series

#### Scenario: Provider is read-only

- **WHEN** the provider serves any read
- **THEN** no INSERT/UPDATE/DELETE is issued against any store it touches

### Requirement: Astock provider implements the standard interface on daily granularity

The provider SHALL implement the `MarketDataProvider` interface with daily granularity:
`get_bars(symbol, "1d", limit)` / `get_daily_bars` return chronological 前复权 daily OHLC
bars (open/high/low/close from concepts 233/234/235/236, volume or amount when available);
`get_quote(symbol)` SHALL derive a quote from the latest daily close with the same
synthetic half-spread derivation as the fd-open-data provider (5 bps each side), returning
`None` when no close exists (data gap, not an error); `get_latest_close` returns the most
recent close. Timeframes other than `1d` are unsupported and SHALL be rejected explicitly.

#### Scenario: Daily bars served for an A-share symbol

- **WHEN** `get_bars("600036", "1d", 250)` is called and the store covers the range
- **THEN** chronological daily bars with real (non-synthesized) OHLC values are returned

#### Scenario: Quote derived from latest close

- **WHEN** the latest daily close for a symbol is `c`
- **THEN** `get_quote` returns `bid < c < ask` with `bid != ask`

#### Scenario: Unsupported timeframe rejected

- **WHEN** a timeframe other than `1d` is requested
- **THEN** the provider raises a clear unsupported-timeframe error rather than returning wrong-granularity data

#### Scenario: No data is a gap

- **WHEN** the store has no rows for a symbol
- **THEN** `get_quote` returns `None` and `get_daily_bars` returns an empty series

### Requirement: Instrument search resolves A-share entities

`search_instruments(query)` SHALL resolve A-share entities by code (e.g. `600036`,
`000333`) and by Chinese name; `get_instrument(symbol)` SHALL return the service's own
instrument value (symbol, display name, venue `XSHG`/`XSHE`, price/quantity precision) for
entities the store knows and `None` otherwise. Orders for A-share symbols SHALL be accepted
only when `get_instrument` resolves — the existing instrument-precondition requirement
applies unchanged.

#### Scenario: Search by code and by name

- **WHEN** the search query is `600036` or `招商银行`
- **THEN** the matching A-share entity is returned with its code, name, and venue

#### Scenario: Unknown symbol stays unknown

- **WHEN** `get_instrument` is asked for a symbol the store does not carry
- **THEN** it returns `None` and order acceptance for that symbol is refused

### Requirement: Provider declares its adjustment caliber in coverage

The provider SHALL implement `get_bars_with_coverage` with the same contract as the
fd-open-data provider (`missing` / `market_closed` / `fill_failed` / `fingerprint`), and the
coverage result SHALL additionally carry the provider's adjustment caliber
(`adjusted: "forward"` for the `astock_daily` family). When the caliber cannot be
determined, the report SHALL say so explicitly rather than defaulting silently.

#### Scenario: Coverage reports the adjustment caliber

- **WHEN** a coverage check completes for a symbol
- **THEN** the report includes `adjusted = "forward"` so consumers can document the caliber the series was read at

#### Scenario: Indeterminable caliber is stated, not defaulted

- **WHEN** the provider cannot establish which caliber it read
- **THEN** the coverage report marks the caliber as unknown instead of asserting a value

### Requirement: Benchmark index series availability is explicit

When index daily series (沪深300 `000300`, 中证500 `000905`) are available through the
provider's data family, `get_daily_bars` SHALL serve them like any other symbol, making the
backtest benchmark comparison end-to-end usable. When an index series is unavailable, the
provider SHALL report a data gap for it — and the backtest's benchmark reporting SHALL
continue to surface that gap as an explicit error/annotation (the existing 报缺纪律),
never a silently omitted benchmark.

#### Scenario: Index series serves the benchmark pipeline

- **WHEN** a backtest requests benchmark `000300` over a window the index family covers
- **THEN** real index daily bars are returned and relative metrics compute end-to-end

#### Scenario: Missing index is a named gap

- **WHEN** the requested benchmark index has no data in the window
- **THEN** the outcome names the missing benchmark and window rather than omitting it

### Requirement: Group data budgets apply unchanged to the astock provider

The user-group data budgets that bound every provider — `max_backtest_days` and the
quote-lookback window that bounds `get_quote` — SHALL bind the astock provider
identically: switching providers SHALL NOT widen any user's data budget or shorten any
staleness bound.

#### Scenario: Backtest window limit holds

- **WHEN** a backtest on the astock provider requests a span beyond the group's `max_backtest_days`
- **THEN** it is refused with the same error semantics as on any other provider

#### Scenario: Staleness bound is provider-agnostic

- **WHEN** the provider's newest close for a symbol is older than the quote lookback window
- **THEN** `get_quote` returns `None` (a data gap) rather than quoting stale prices, the same rule every provider obeys
