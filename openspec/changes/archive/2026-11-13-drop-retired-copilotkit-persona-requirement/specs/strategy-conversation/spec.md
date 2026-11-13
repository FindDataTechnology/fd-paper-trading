# strategy-conversation Delta

## REMOVED Requirements

### Requirement: CopilotKit runtime prompt gains strategy persona
**Reason**: 该 runtime 已于 2026-10-14 随 commit `d3dec0b` 有意移除（"the platform pack's persona replaces it; the SPA keeps dashboard/orders/charts/strategies"）。本仓不再有 `web/copilot-runtime/`，也没有任何 system prompt 可检视——这条需求自删除当天起即不可满足。persona 现在归属平台 pack，不在本仓规格范围内；同一次删除里 `web-copilotkit-chat` 的 Purpose 已改记为"历史约束记录"（`8e092f1`），此处与那项裁决对齐。

## MODIFIED Requirements

### Requirement: Conversational build-backtest-run loop

Every chat surface that consumes the fd-paper-trading MCP server (Claude Code via MCP; any platform-side persona) SHALL support the full loop in conversation: choose a template or write code, create the strategy, run a backtest, inspect stats, start/stop a paper run on a chosen account, and review run history. `start_strategy` SHALL require an explicit `account_id`; `list_accounts` remains the discovery path.

#### Scenario: Create then backtest in one conversation
- **WHEN** a user asks the assistant to build an SMA-crossover strategy on a symbol and backtest it over the past year
- **THEN** the assistant calls `list_strategy_templates`, `create_strategy`, and `run_backtest`, and reports the resulting stats

#### Scenario: Start requires an account
- **WHEN** `start_strategy` is called without an `account_id`
- **THEN** the tool returns an error prompting for an account (discoverable via `list_accounts`)
