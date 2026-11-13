## 1. 测试（承重墙先有覆盖）

- [x] 1.1 `tests/test_orders.py` 补 `no_quote` 与 `unknown_instrument` 两条拒单用例（此前两条原因零覆盖）
- [x] 1.2 注入对照：撤掉 service.py 的 `no_quote` 闸 → 新用例红（1 failed）；还原 → `diff` 逐字节一致、12 passed

## 2. 规格

- [ ] 2.1 写 delta（保留场景原名，正文改指向下单入口的两道闸）
- [ ] 2.2 `openspec validate align-astock-instrument-resolution --strict`
- [ ] 2.3 `openspec archive ... --yes`，对账 `~ 1 modified`
- [ ] 2.4 `openspec validate astock-market-data --type spec --strict`
