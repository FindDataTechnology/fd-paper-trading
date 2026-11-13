## MODIFIED Requirements

### Requirement: Environment variable names do not embed the old identifier

Environment variables SHALL NOT carry the retired name. Variables whose names are generic
(`DATABASE_URL`) SHALL remain unchanged; the market-data selection variable is
`MARKET_DATA_SOURCE_ID` (the legacy `MARKET_DATA_PROVIDER` was removed and fails startup when
still set); the MCP client's
connection variables SHALL be `PAPER_TRADING_API_URL` and `PAPER_TRADING_API_TOKEN`.

#### Scenario: MCP client configuration uses the new names

- **WHEN** the MCP server process starts with only `PAPER_TRADING_API_TOKEN` set
- **THEN** it resolves its upstream API credential from that variable

#### Scenario: Old variable name is not silently honoured

- **WHEN** the MCP server process is started with `PAPER_TRADING_API_TOKEN` set and
  `PAPER_TRADING_API_TOKEN` unset
- **THEN** it does not treat the old variable as its credential source
