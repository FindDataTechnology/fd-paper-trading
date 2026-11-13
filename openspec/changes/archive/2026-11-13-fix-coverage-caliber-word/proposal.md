## Why

`astock-market-data` 里有一条**已经与实现对不上**的规格句：它要求 coverage 报
`adjusted: "forward"`，而自 `8ada860`（fix(caliber): coverage 的口径改读声明）起，
provider 报的是 `declarations.yaml` 里**声明**的口径——`astock_daily` 声明为 `qfq`。

2026-11-13 在生产 pod 里实测：

```
PASS: coverage caliber matches the declaration — adjusted=qfq, declared=qfq
```

这句规格此前没被发现的直接原因是：`scripts/smoke_astock_provider.py` 里那条断言写死
`== "forward"`，而该脚本自 `add-price-supply-and-caliber` 起就因为
`AstockMarketDataProvider(dsn=dsn)` 缺必填 `source=` 而**启动即崩**、从未真正运行过
（见 2026-11-13 的 `c1f03dc`）。规格、脚本、实现三处各有一份"前复权"的写法，
谁都没在跑，所以谁都没发现它们不一样。

## What Changes

- 规格句改为：coverage 报**声明**的口径（`astock_daily` 是 `qfq`，即契约词表里的前复权），
  模块常量只作"没有注册表时"的回退；口径不可判时仍须如实标注。
- 保留两条场景的**原名**（重命名会被归档判为删除），只更正场景正文里的字面量。
- 实现无需改动：`8ada860` 的读声明方向是对的（契约词用 `qfq`），
  `c1f03dc` 已把冒烟断言改成与 `_declared_adjustment()` 和契约词表
  `PRICE_ADJUSTMENTS` 比对，不再自带一份字面量副本。

## Capabilities

### Modified Capabilities

- `astock-market-data`: 「Provider declares its adjustment caliber in coverage」一条的口径取值
  从写死的人话 `forward` 改为**声明值**（`qfq`），并要求与注册表声明一致。
