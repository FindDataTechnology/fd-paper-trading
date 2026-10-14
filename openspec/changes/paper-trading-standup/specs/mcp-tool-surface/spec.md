## Purpose

The service's MCP surface: which tools it exposes, how a caller's identity is carried from
the platform to this service, and the 上牌 obligations that make the server referenceable
by packs and drivable by agents.

## ADDED Requirements

### Requirement: Tool inventory

The MCP server SHALL expose these tools: account and order operations (`create_account`,
`list_accounts`, `get_account`, `list_positions`, `place_order`, `cancel_order`,
`list_orders`), market inspection (`search_instruments`, `get_bars`), and strategy
operations (`list_strategy_templates`, `create_strategy`, `update_strategy`,
`list_strategies`, `get_strategy`, `run_backtest`, `get_strategy_run`,
`list_strategy_runs`, `start_strategy`, `stop_strategy`). Every tool SHALL be available on
both transports (stdio and streamable HTTP) with no transport-specific code.

#### Scenario: Account lifecycle is reachable from conversation

- **WHEN** an agent lists tools
- **THEN** `create_account` is present, so an agent can open a paper account for a caller
  who has none, without resorting to the web interface

#### Scenario: Position detail is reachable from conversation

- **WHEN** an agent lists tools
- **THEN** `list_positions` is present, so per-symbol holdings, average cost, and
  unrealized P&L are observable rather than only the account-level cash figure

#### Scenario: Market inspection is reachable from conversation

- **WHEN** an agent lists tools
- **THEN** `search_instruments` and `get_bars` are present, so symbol discovery and
  historical series are available for analysis

#### Scenario: Both transports expose the same set

- **WHEN** a client connects over stdio and another over streamable HTTP
- **THEN** both list the same tool set

### Requirement: Cash adjustments are not exposed as tools

The MCP surface SHALL NOT expose the deposit or account-reset operations. Opening an
account SHALL establish its starting balance; later cash adjustments and resets SHALL
remain reachable only through the authenticated REST/web surface.

#### Scenario: No deposit tool

- **WHEN** the MCP tool list is inspected
- **THEN** no tool deposits funds or resets an account

#### Scenario: Opening an account establishes the balance

- **WHEN** an agent creates an account without specifying a starting balance
- **THEN** the account is created with the configured default starting balance

### Requirement: Caller identity is delegated, not substituted

The MCP server SHALL forward the inbound request's `Authorization` credential to the REST
API, so that each caller acts as themselves. When an inbound request carries no credential
— the stdio case — the server SHALL fall back to the operator-configured token. The server
SHALL NOT substitute a single configured identity for an inbound credential that is
present.

#### Scenario: Distinct callers act as distinct users

- **WHEN** two callers with different credentials call the same tool on one running server
- **THEN** each call reaches the API as its own caller, and neither can observe the other's
  accounts, orders, positions, or strategies

#### Scenario: Inbound credential wins over configuration

- **WHEN** a request arrives carrying a credential while a fallback token is also configured
- **THEN** the inbound credential is used for that call

#### Scenario: stdio keeps working without a header

- **WHEN** a local client connects over stdio and the operator-configured token is set
- **THEN** tool calls succeed using that token

#### Scenario: No credential anywhere fails closed

- **WHEN** a call arrives with no inbound credential and no operator-configured token
- **THEN** the call fails with an authorization error rather than reaching the API
  unauthenticated

#### Scenario: Inbound credential is read from the raw request

- **WHEN** the server reads the inbound credential
- **THEN** it obtains it from the raw HTTP request headers, not from a header accessor that
  strips authorization

### Requirement: Upstream errors remain self-repairing

Tool failures SHALL be returned to the caller as structured results carrying a machine
code and message (and field-level detail where the API provides it), so a model can correct
its arguments and retry in the same conversation. Authorization failures SHALL be returned
distinguishably from validation failures.

#### Scenario: Validation error enumerates fields

- **WHEN** a tool is called with arguments the API rejects as invalid
- **THEN** the result carries the API's error code and the offending fields

#### Scenario: Authorization failure is distinguishable

- **WHEN** the upstream API rejects the credential
- **THEN** the result reports an authorization code rather than a generic failure

### Requirement: 上牌 manifest and selfcheck

The repository SHALL maintain a tool-surface manifest declaring every tool's exposure
level and the hosting forms the server supports, plus a selfcheck that asserts both
directions: the manifest agrees with the registered tool set, and any declared commercial
exposure is a subset of the manifest. The manifest SHALL declare the hosting form the
service is actually deployed in.

#### Scenario: Manifest matches the registered tools

- **WHEN** the selfcheck runs after a tool is added or removed without updating the manifest
- **THEN** it fails and names the differing tools

#### Scenario: Manifest declares hosting form

- **WHEN** the manifest is inspected
- **THEN** it declares the shared-instance hosting form with the mesh upstream address and
  the expected tool count

#### Scenario: Server is eligible for pack reference

- **WHEN** a pack declares tools from this server
- **THEN** the declared tools fall within the manifest's commercial set
