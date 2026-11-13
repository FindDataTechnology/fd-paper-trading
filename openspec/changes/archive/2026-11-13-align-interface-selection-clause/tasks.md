## 1. 规格

> 2026-11-13 复核补记：归档时五项未勾。逐条核过——
>
> - 1.1 delta 已生效：`market-data`「Provider is swappable」现为
>   「Selection SHALL be via `MARKET_DATA_SOURCE_ID`, a source id registered in the
>   price-source registry: the declared `access.kind` decides the provider class …」；
> - 1.2/1.3 是那次归档自身的动作，归档记录即证据（无法重跑）；
> - 1.4 复核：`openspec validate market-data --type spec --strict` → exit 0；
> - **1.5 的原判据已被取代，不是未通过**：当时写的是"全仓 grep 应零命中"，而后来的
>   `purge-dead-selector-from-specs` 决定在正文里**点名**这三个旧变量（"'I changed it'
>   与 'it never took effect' 不能长得一样"）。今天的现行判据是
>   「只应剩'已移除/失败启动'的陈述」（见该 change 的 1.6）。按现行判据：3 处命中，
>   全部是 removal 陈述 ⇒ 满足。原判据零命中在今天既不可能也不该追求。

- [x] 1.1 写 delta（保留两条场景原名，只改需求句尾的选择机制）
- [x] 1.2 `openspec validate align-interface-selection-clause --strict`
- [x] 1.3 `openspec archive ... --yes`，对账 `~ 1 modified`
- [x] 1.4 `openspec validate market-data --type spec --strict`
- [x] 1.5 全仓复核（判据后经 `purge-dead-selector-from-specs` 1.6 修订为"只剩 removal 陈述"，
      现行判据下满足：3 处命中全是 removal 陈述）
