## Why

`market-data` 的「Provider is swappable」仍在描述一个**已被删除**的选择机制：

> Selection SHALL be via the `MARKET_DATA_PROVIDER` environment variable.

而 `add-price-supply-and-caliber` task 6.4（commit `1c1cca8`）已经把
`MARKET_DATA_PROVIDER` / `ASTOCK_SOURCE` / `ASTOCK_CALENDAR_REFS` 的映射层整段删除，
`MARKET_DATA_SOURCE_ID` 成了唯一选择器，**带着旧变量启动会直接失败**。那次 change 的
delta 只写了四条新能力（`price-source-registry` / `price-caliber-assertion` /
`multi-source-observations` / `index-price-series`），没有回改这条既有需求——于是规格停在
两个版本之前，而它的场景还在教人设一个会拒绝启动的变量。

2026-11-13 在生产 pod 实测四种启动行为（不靠读代码推断）：

```
空值                                  → provider=simulated
MARKET_DATA_SOURCE_ID=astock_daily    → provider=federated-view source_id='astock_daily'
MARKET_DATA_SOURCE_ID=does-not-exist  → SourceSelectionError: ... is not registered
MARKET_DATA_PROVIDER=polygon          → SourceSelectionError: legacy market-data variable(s) still set
```

注意最后一条：规格里那个"设为 polygon 就用 PolygonMarketDataProvider"的场景，今天照着做
**根本起不来**。而倒数第二条说明"unset/unknown → simulated"也不成立——unknown 现在是
**启动失败**，不是静默降级。这正是 6.4 有意消灭的形态："我改过了"与"其实没生效"不能长得一样。

## What Changes

- 需求句改为：选择经 `MARKET_DATA_SOURCE_ID`（注册表 id）解析，协议由声明的
  `access.kind` 决定 provider 类；未注册 id 是**启动错误**，不是静默回退；未设置则用
  simulated。
- 场景「Selecting the provider at startup」**保留原名**（重命名会被归档判为删除），
  正文换成上述四种实测行为，并把"旧变量仍在环境里 → 启动失败"写成契约的一部分。
- 代码不改：实现已经是这个形状，且 `tests/marketdata/test_source_selection.py`
  已覆盖旧变量拒绝。

## Capabilities

### Modified Capabilities

- `market-data`: 「Provider is swappable」的选择机制从 `MARKET_DATA_PROVIDER`
  改为注册表 `MARKET_DATA_SOURCE_ID` + `access.kind` 映射，并把"未注册即失败""旧变量即失败"
  写进场景。
