## Why

`market-data` 的「MarketDataProvider interface」需求末尾还有**第二处**在教人设已删除的变量：

> The provider is selected at app startup from
> `MARKET_DATA_PROVIDER` (`simulated` | `polygon` | `fd-open-data`).

它是上一条 change（`align-provider-selection-with-registry`）漏掉的同族句子：那句话只改了
「Provider is swappable」一条。这里连枚举值也是旧的——既没有 `astock`，也没有注册表 id
（`astock_daily` / `akshare` / `index-seed`），而今天就这三条是真实存在的源。

处置单独成一个 change，而不是塞回上一份 delta 再归档一次：同一个能力连续两次 MODIFIED
同一个文件，最容易踩的就是"两份 `## MODIFIED Requirements` 只落最后一份"那个坑。

## What Changes

- 该需求句尾的选择机制改为指向「Provider is swappable」里的注册表口径（`MARKET_DATA_SOURCE_ID`
  + 声明的 `access.kind`），不再重复枚举一份会漂移的 provider 名单。
- 两条场景（`Quote carries bid and ask` / `Data gap signaled as None`）原名保留，正文不动。
- 代码不改。

## Capabilities

### Modified Capabilities

- `market-data`: 「MarketDataProvider interface」删掉过期的选择器枚举，改为引用注册表选择口径。
