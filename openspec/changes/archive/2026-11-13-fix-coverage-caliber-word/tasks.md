## 1. 实现侧（已完成，证据见 c1f03dc）

- [x] 1.1 冒烟断言改为与 `p._declared_adjustment()` 比对，并断言取值落在契约词表 `PRICE_ADJUSTMENTS` 内——检查器不再自带一份真值副本
- [x] 1.2 provider 的 D4 docstring 同步为"读声明"（`_ADJUSTED` 只是无注册表时的回退）
- [x] 1.3 真库实测 `adjusted=qfq, declared=qfq`，报告 `reports/smoke-astock-2026-11-13.txt` 16 项 ALL PASS

## 2. 规格侧

- [ ] 2.1 写 delta：把口径取值的字面量改为声明值（保留两条场景原名）
- [ ] 2.2 `openspec validate fix-coverage-caliber-word --strict`
- [ ] 2.3 `openspec archive fix-coverage-caliber-word --yes`，对账 `~ 1 modified`
- [ ] 2.4 `openspec validate astock-market-data --type spec --strict` 复核
