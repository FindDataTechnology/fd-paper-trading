## 1. 实现侧（已完成，证据见 c1f03dc）

- [x] 1.1 冒烟断言改为与 `p._declared_adjustment()` 比对，并断言取值落在契约词表 `PRICE_ADJUSTMENTS` 内——检查器不再自带一份真值副本
- [x] 1.2 provider 的 D4 docstring 同步为"读声明"（`_ADJUSTED` 只是无注册表时的回退）
- [x] 1.3 真库实测 `adjusted=qfq, declared=qfq`，报告 `reports/smoke-astock-2026-11-13.txt` 16 项 ALL PASS

## 2. 规格侧

> 2026-11-13 复核补记：归档时 2.1-2.4 未勾。核过——
>
> - 2.1 delta 已生效：`astock-market-data:86-88` 现为「coverage result SHALL additionally
>   carry the provider's adjustment caliber **as declared by the price-source registry** —
>   for the `astock_daily` family the declaration is `qfq`（the contract vocabulary is
>   `qfq` / `hfq` / `none` / `not_applicable`）」，两条场景原名保留；
> - 2.2/2.3 是那次归档自身的动作（归档记录即证据，无法重跑）；
> - 2.4 复核：`openspec validate astock-market-data --type spec --strict` → exit 0。

- [x] 2.1 写 delta：把口径取值的字面量改为声明值（保留两条场景原名）
- [x] 2.2 `openspec validate fix-coverage-caliber-word --strict`
- [x] 2.3 `openspec archive fix-coverage-caliber-word --yes`，对账 `~ 1 modified`
- [x] 2.4 `openspec validate astock-market-data --type spec --strict` 复核
