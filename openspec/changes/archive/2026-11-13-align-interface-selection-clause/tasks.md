## 1. 规格

- [ ] 1.1 写 delta（保留两条场景原名，只改需求句尾的选择机制）
- [ ] 1.2 `openspec validate align-interface-selection-clause --strict`
- [ ] 1.3 `openspec archive ... --yes`，对账 `~ 1 modified`
- [ ] 1.4 `openspec validate market-data --type spec --strict`
- [ ] 1.5 全仓复核：`grep -rn "MARKET_DATA_PROVIDER" openspec/specs/` 应零命中
