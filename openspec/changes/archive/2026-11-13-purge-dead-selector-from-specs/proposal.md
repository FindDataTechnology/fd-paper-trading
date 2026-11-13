## Why

`MARKET_DATA_PROVIDER` 在 `add-price-supply-and-caliber` task 6.4（commit `1c1cca8`）已被删除，
现在**带着它启动会直接失败**。前两个 change 只修掉了直接描述选择机制的两句
（`Provider is swappable`、`MarketDataProvider interface`），随后全仓 `grep` 还剩三处引用，
每一处都会把人引到那个会拒绝启动的变量上：

| 位置 | 现在的写法 | 问题 |
|---|---|---|
| `market-data` 「One price world…」 | `WHEN MARKET_DATA_PROVIDER=fd-open-data` | 这个 WHEN 已不可满足——该需求整体变成死条款 |
| `service-deployment` 「只读数据源凭据经部署环境注入」 | `WHEN 部署未注入该 DSN 且 MARKET_DATA_PROVIDER 不是该 provider` | 判据挂在一个没人能设的变量上 |
| `service-identity` 「Environment variable names…」 | `generic (DATABASE_URL, MARKET_DATA_PROVIDER) SHALL remain unchanged` | 说它"保持不变"，而它已被删除——**方向正好相反** |

第三条最值得记：它不是过时，而是**说反了**。规格说那个名字应当不变，事实是那个名字已被
移除并会让启动失败。

## What Changes

- `market-data`「One price world…」：条件从"设了某个环境变量"改为"无论启动选择解析到哪个
  provider"——需求原意（一个进程只用一个价格世界）保留，去掉已死的触发条件。
- `service-deployment`「只读数据源凭据…」：条件改为"`MARKET_DATA_SOURCE_ID` 未选择需要该 DSN 的源"。
- `service-identity`「Environment variable names…」：把 `MARKET_DATA_PROVIDER` 从"保持不变"
  的名单里移除（只留 `DATABASE_URL`），并写明选择变量现为 `MARKET_DATA_SOURCE_ID`。
- 三处场景**原名全部保留**（重命名会被归档判为删除），只改正文。
- 代码不改：实现早已如此。

## Capabilities

### Modified Capabilities

- `market-data`: 「One price world for backtest, live, and manual fills」去掉已死的 WHEN 条件
- `service-deployment`: 「只读数据源凭据经部署环境注入」的场景条件改用注册表选择器
- `service-identity`: 「Environment variable names do not embed the old identifier」把
  `MARKET_DATA_PROVIDER` 从"保持不变"名单移除
