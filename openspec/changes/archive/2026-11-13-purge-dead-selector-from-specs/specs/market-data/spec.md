## MODIFIED Requirements

### Requirement: One price world for backtest, live, and manual fills

Whichever provider the startup selection resolves to (see "Provider is swappable"), that same
provider instance SHALL feed strategy backtests (historical daily bars), live strategy ticks
(latest bar), and order fills (quote). Consumers SHALL NOT mix price series across providers
within one app process.

#### Scenario: Backtest and fills share the series
- **WHEN** a strategy is backtested and its signals reference bar closes
- **THEN** manual and live orders on the same symbol fill against quotes derived from that same series' closes
