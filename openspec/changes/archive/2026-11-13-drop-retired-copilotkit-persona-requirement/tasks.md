## 1. 规格修正

- [x] 1.1 核实 runtime 确已删除（`git log --diff-filter=D -- web/copilot-runtime` → `d3dec0b`）
- [x] 1.2 核实本仓零残留（`web/src`、`package.json`、`src/` 均无 copilot/chat 实现）
- [x] 1.3 移除 `CopilotKit runtime prompt gains strategy persona`
- [x] 1.4 修订 `Conversational build-backtest-run loop` 的 chat 面表述（场景逐字保留）
- [x] 1.5 `.env.example` 里对应的 OPENAI_API_KEY / OPENAI_API_BASE / COPILOT_MODEL 已删（commit `d75ec1a`）
