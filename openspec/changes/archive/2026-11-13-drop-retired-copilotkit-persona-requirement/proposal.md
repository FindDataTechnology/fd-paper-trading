## Why

`strategy-conversation` 的第四条需求要求「The CopilotKit runtime's system prompt SHALL describe
the strategy tools …」。而那个 runtime 已在 **2026-10-14** 的 commit `d3dec0b` 里被有意删除：

> Removes the bundled CopilotKit runtime and SPA chat: the platform pack's persona
> replaces it; the SPA keeps dashboard/orders/charts/strategies.

今天本仓没有 `web/copilot-runtime/`、没有任何 system prompt 可检视，`web/src` 里连
`copilot` 这个词都搜不到。也就是说：这条需求从被删除的当天起就是**不可满足的陈述**，
而它所在的 capability 其余三条仍在服役（MCP 十工具、结构化错误、对话式闭环），
所以不是"整块作废"，只是这一条与事实相反。

同一条 capability 里还有一句更轻的漂移：「Conversational build-backtest-run loop」把
chat 面写成「Claude Code via MCP, **web chat via CopilotKit** consuming the same MCP
server」。MCP 那一半是活的，CopilotKit 那一半随同一次删除消失了。

（`web-copilotkit-chat` 整份 capability **不动**：2026-10-14 `8e092f1` 已把它的 Purpose
改写为「已随独立化移除，本 spec 保留为历史约束记录」——那是明确的裁决，不是漂移。）

## What Changes

- **REMOVED**（strategy-conversation）：`CopilotKit runtime prompt gains strategy persona`
  —— runtime 不存在，persona 现在归属平台 pack，不在本仓规格范围。
- **MODIFIED**（strategy-conversation）：`Conversational build-backtest-run loop`
  —— 把 chat 面从"CopilotKit 的 web chat"改为"任何消费本仓 MCP server 的 chat 面"，
  两个既有场景逐字保留（场景名不可改名）。
