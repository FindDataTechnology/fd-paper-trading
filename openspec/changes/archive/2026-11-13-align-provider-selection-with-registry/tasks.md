## 1. 实测（先有读数再写规格）

- [x] 1.1 生产 pod 实测四种启动行为：空值→simulated；已注册→federated-view；未注册→SourceSelectionError；旧变量→SourceSelectionError

## 2. 规格

> 2026-11-13 复核补记：归档时 2.1-2.4 未勾。核过——
>
> - 2.1 delta 已生效：`market-data`「Provider is swappable」的正文与三个 AND-WHEN 场景
>   现在逐条对应那条实测（空值→simulated；未注册→启动失败并点名；旧变量→启动失败并点名；
>   已注册→按 `access.kind` 选 provider）；
> - 2.2/2.3 是那次归档自身的动作（归档记录即证据，无法重跑）；
> - 2.4 复核：`openspec validate market-data --type spec --strict` → exit 0。

- [x] 2.1 写 delta（保留场景原名，正文换成实测行为）
- [x] 2.2 `openspec validate align-provider-selection-with-registry --strict`
- [x] 2.3 `openspec archive ... --yes`，对账 `~ 1 modified`
- [x] 2.4 `openspec validate market-data --type spec --strict`
