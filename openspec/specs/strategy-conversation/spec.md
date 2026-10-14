# strategy-conversation Specification

## Purpose

约束策略 MCP 工具面：模板目录/策略 CRUD/回测/纸面实盘十工具、LLM 可自修复的结构化错误、双传输一致性。
## Requirements
### Requirement: Strategy MCP tools

The fd-paper-trading MCP server SHALL expose these tools, each calling the authenticated REST API with the configured token: `list_strategy_templates` (template catalog incl. per-template JSON Schema), `create_strategy`, `update_strategy`, `list_strategies`, `get_strategy`, `run_backtest`, `get_strategy_run`, `start_strategy`, `stop_strategy`, `list_strategy_runs`. The tools SHALL be available on both existing transports (stdio and HTTP/SSE) without transport-specific code.

#### Scenario: Catalog tool returns schemas
- **WHEN** an LLM calls `list_strategy_templates`
- **THEN** it receives each template's id, description, and parameter JSON Schema sufficient to fill params without guessing

#### Scenario: Tools visible on both transports
- **WHEN** a client connects via stdio or via HTTP/SSE
- **THEN** the same ten strategy tools are listed

### Requirement: Structured validation errors for LLM self-repair

When a strategy tool call fails validation (bad spec shape, invalid template params, sandbox-rejected source, group-limit rejection), the tool SHALL return a structured error whose message enumerates every violation in terms an LLM can act on (field names, allowed values, valid template ids), enabling the model to correct and retry in the same conversation.

#### Scenario: Invalid template params enumerated
- **WHEN** `create_strategy` is called with `params: {"fast": -3, "slow": "abc"}` for `sma_cross`
- **THEN** the tool error names both invalid fields and their constraints

#### Scenario: Sandbox rejection explains the rule
- **WHEN** `create_strategy` is called with source containing `import os`
- **THEN** the tool error states that imports are forbidden and data must come from `ctx`

### Requirement: Conversational build-backtest-run loop

The chat surfaces (Claude Code via MCP, web chat via CopilotKit consuming the same MCP server) SHALL support the full loop in conversation: choose a template or write code, create the strategy, run a backtest, inspect stats, start/stop a paper run on a chosen account, and review run history. `start_strategy` SHALL require an explicit `account_id`; `list_accounts` remains the discovery path.

#### Scenario: Create then backtest in one conversation
- **WHEN** a user asks the assistant to build an SMA-crossover strategy on a symbol and backtest it over the past year
- **THEN** the assistant calls `list_strategy_templates`, `create_strategy`, and `run_backtest`, and reports the resulting stats

#### Scenario: Start requires an account
- **WHEN** `start_strategy` is called without an `account_id`
- **THEN** the tool returns an error prompting for an account (discoverable via `list_accounts`)

### Requirement: CopilotKit runtime prompt gains strategy persona

The CopilotKit runtime's system prompt SHALL describe the strategy tools, the template catalog workflow (list templates -> fill params -> create), and the code-track `ctx` contract (available bar accessors, indicators, `ctx.position`/`ctx.cash`, `ctx.buy`/`ctx.sell` with optional `limit_price`, no imports), so the web chat assistant can author valid strategies without hallucinating the API.

#### Scenario: Prompt documents the ctx contract
- **WHEN** the CopilotKit runtime prompt is inspected
- **THEN** it enumerates the ctx accessors and the no-imports rule
