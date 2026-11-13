## 1. 规格（三个 capability，每个一份 delta、每个文件一个 MODIFIED）

- [ ] 1.1 `market-data`：One price world 的条件去死
- [ ] 1.2 `service-deployment`：凭据场景条件改用 `MARKET_DATA_SOURCE_ID`
- [ ] 1.3 `service-identity`：把 `MARKET_DATA_PROVIDER` 从"保持不变"名单移除
- [ ] 1.4 `openspec validate purge-dead-selector-from-specs --strict`
- [ ] 1.5 `openspec archive ... --yes`，对账 `~ 3 modified`
- [ ] 1.6 三个 capability 各跑一次 `--strict`；全仓 `grep MARKET_DATA_PROVIDER openspec/specs/`
      只应剩"已移除"的陈述
