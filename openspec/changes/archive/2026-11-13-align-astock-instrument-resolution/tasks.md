## 1. 测试（承重墙先有覆盖）

- [x] 1.1 `tests/test_orders.py` 补 `no_quote` 与 `unknown_instrument` 两条拒单用例（此前两条原因零覆盖）
- [x] 1.2 注入对照：撤掉 service.py 的 `no_quote` 闸 → 新用例红（1 failed）；还原 → `diff` 逐字节一致、12 passed

## 2. 规格

> 2026-11-13 复核补记：归档时以下各项未勾。逐条对**当下 spec 正文**核过——
>
> - 2.1 delta 已生效：`astock-market-data` 的 Instrument resolution 现为句法解析 +
>   下单入口两道闸（`unknown_instrument` / `no_quote`），两处命中；
> - 2.2/2.3 是那次归档自身的动作，归档记录即证据（无法重跑）；
> - 2.4 复核：`openspec validate astock-market-data --type spec --strict` → exit 0。

- [x] 2.1 写 delta（保留场景原名，正文改指向下单入口的两道闸）
- [x] 2.2 `openspec validate align-astock-instrument-resolution --strict`
- [x] 2.3 `openspec archive ... --yes`，对账 `~ 1 modified`
- [x] 2.4 `openspec validate astock-market-data --type spec --strict`
