## 1. 实测（先有读数再写规格）

- [x] 1.1 生产 pod 实测四种启动行为：空值→simulated；已注册→federated-view；未注册→SourceSelectionError；旧变量→SourceSelectionError

## 2. 规格

- [ ] 2.1 写 delta（保留场景原名，正文换成实测行为）
- [ ] 2.2 `openspec validate align-provider-selection-with-registry --strict`
- [ ] 2.3 `openspec archive ... --yes`，对账 `~ 1 modified`
- [ ] 2.4 `openspec validate market-data --type spec --strict`
