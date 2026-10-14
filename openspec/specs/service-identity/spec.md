# service-identity Specification

## Purpose
The naming contract for a published paper-trading service: which identifiers are free to
choose before the first release, and which of them become permanent runtime contracts the
moment the service is published.

## Requirements

### Requirement: Service identifier set

The service SHALL use one identifier, `fd-paper-trading`, for its repository and directory
name, its MCP server name, and its console script. Its import package name SHALL be
`fd_paper_trading`, derived from the directory name by the workspace convention (dashes for
directories and distributions, underscores for importable packages). Its default database
name SHALL be `paper_trading`.

#### Scenario: All identifiers agree

- **WHEN** the repository is inspected after this change
- **THEN** the directory is `fd-paper-trading`, the distribution name is `fd-paper-trading`,
  the importable package is `fd_paper_trading`, the console script is `fd-paper-trading`,
  and the default database URL names the `paper_trading` database

#### Scenario: No old identifier survives in shipped code

- **WHEN** the tracked working tree is searched for `fd_paper_trading`, `fd-paper-trading`, and `paper_trading`
  outside `openspec/changes/archive/`
- **THEN** no match is found

#### Scenario: Archive keeps its history

- **WHEN** the archived change proposals under `openspec/changes/archive/` are inspected
- **THEN** they still contain the identifiers that were accurate when they were written

### Requirement: MCP server name is the tool namespace contract

The MCP server name SHALL be `fd-paper-trading`, so that client-visible tool identifiers
take the form `mcp__fd-paper-trading__<tool>`. Once published this name SHALL NOT change,
because pack manifests, skill texts, and registry entries reference it.

#### Scenario: Tools are namespaced under the new name

- **WHEN** a client lists tools from the running server
- **THEN** every tool is addressable as `mcp__fd-paper-trading__<tool>`

#### Scenario: Published name is frozen

- **WHEN** a later change proposes renaming the MCP server
- **THEN** it must be treated as a breaking change to packs and skills, not as a facade edit

### Requirement: API token prefix is preserved

The long-lived API-token prefix SHALL remain `fdt_`. Token validity is decided by prefix
dispatch, so changing it would invalidate every issued token while adding no value; the
prefix is read as find**d**ata **t**rade.

#### Scenario: Prefix dispatch still works

- **WHEN** a request presents a token beginning with `fdt_`
- **THEN** the auth layer resolves it as an API token rather than as a JWT

#### Scenario: Pre-existing tokens remain valid

- **WHEN** a token issued before this change is presented after it
- **THEN** it authenticates successfully

### Requirement: Environment variable names do not embed the old identifier

Environment variables SHALL NOT carry the retired name. Variables whose names are generic
(`DATABASE_URL`, `MARKET_DATA_PROVIDER`) SHALL remain unchanged; the MCP client's
connection variables SHALL be `PAPER_TRADING_API_URL` and `PAPER_TRADING_API_TOKEN`.

#### Scenario: MCP client configuration uses the new names

- **WHEN** the MCP server process starts with only `PAPER_TRADING_API_TOKEN` set
- **THEN** it resolves its upstream API credential from that variable

#### Scenario: Old variable name is not silently honoured

- **WHEN** the MCP server process is started with `PAPER_TRADING_API_TOKEN` set and
  `PAPER_TRADING_API_TOKEN` unset
- **THEN** it does not treat the old variable as its credential source

### Requirement: Public package surface

The distribution SHALL declare the MIT license and SHALL ship a `LICENSE` file at the
repository root. The Chinese README SHALL be named `README.zh-CN.md` and SHALL link to the
English README, which SHALL link back.

#### Scenario: License is declared and present

- **WHEN** the package metadata and the repository root are inspected
- **THEN** the metadata declares MIT and a `LICENSE` file containing the MIT text exists

#### Scenario: Bilingual README pair

- **WHEN** the repository root is inspected
- **THEN** `README.md` and `README.zh-CN.md` both exist and cross-link
