## Why

`astock-market-data` 的「Instrument search resolves A-share entities」要求
`get_instrument(symbol)` 对库里没有的标的一律返回 `None`，场景名还叫
「Unknown symbol stays unknown」。实现不是这样，而且**是有意为之**（`astock.py:700`
的 docstring 写明了理由）：接口是同步的、库是异步的，A 股目录本身是"全交易所句法空间"，
所以句法合法的代码一律返回服务自己的 Instrument，真正的拦截放在下单入口的报价闸。

2026-11-13 生产 pod 实测：

```
FAIL: Unknown symbol stays unknown — get_instrument(999999)=Instrument(symbol='999999', ...)
```

但同一次实测里，**下单确实被拒**：`execution/service.py` 的入口有两道闸，未知代码会落在
`no_quote`（或 `unknown_instrument`）。

通用规格 `market-data` 的对应场景本来就允许这种实现——它写的是
「**THEN** the outcome is `None` **or a rejection that prevents order acceptance**」。
所以不一致只存在于 astock 这一份更严的措辞里，不是跨能力的冲突。

更重要的是：**这句话把约束挂在了不会失败的那一侧**。按现规格"修" `get_instrument` 返回 None
不会让任何测试红，而删掉 `no_quote` 那道闸会让未知标的变成可下单——此前两条拒单原因
**零测试覆盖**（本次一并补上，且已用注入对照证明撤闸即红）。

## What Changes

- astock 的需求句改为：`get_instrument` 对本 capability 的目录做**句法解析**（同步接口 +
  异步库的约束），不存在性由**下单入口**的 `unknown_instrument` / `no_quote` 两道拒单保证。
- 场景「Unknown symbol stays unknown」**保留原名**（重命名会被归档判为删除），
  正文改为"下单被拒，且拒因点名"，把那两道闸写成契约的一部分。
- 代码不改：现有实现符合通用契约，且行为已被新测试钉住。

## Capabilities

### Modified Capabilities

- `astock-market-data`: 「Instrument search resolves A-share entities」一条把"未知"的处置
  从"`get_instrument` 返回 None"改为"下单入口拒单（`unknown_instrument` / `no_quote`）"。
