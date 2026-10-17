## Context

动机与实测证据见 `proposal.md`。设计前先钉住四条读自本仓/DAAS 的事实：

- **本仓只拦完全无数据**：`api/routes/strategies.py` 的 `if not bars` / `if not any(bars_by_symbol.values())`，没有任何部分覆盖检查。
- **DAAS 已能如实报覆盖度**（`astock-price-on-demand`，33/33）：`fill_gaps()` 返回 `points` / `gaps` / `fill_attempted` / `fill_failed` / `fingerprint`。
- **`gaps` 里混着两种事实**：①**休市**（问过上游，确实没有数据）②**真缺失**（没有，也没证明没有）。DAAS 侧已补 `missing`（前者之外）与 `market_closed`（已探明空）两个字段把它们分开——**消费方不得自己再判一遍**，否则就是把交易日历的规则复制成第二份。
- **只有真实数据源有覆盖度能力**：`get_daily_bars` 只在 `FdOpenDataMarketDataProvider` 上；其余 provider 是模拟的（确定性合成序列，永远"完整"）。门禁若对模拟 provider 生效，全部既有测试立刻变红——那不是发现了问题，是测试在报告一个不存在的缺口。

## Goals / Non-Goals

**Goals:**
- 部分覆盖的回测**不可能**再静默产出一个看起来正常的收益率。
- 拒绝时**点名**哪个标的、缺哪一段，让人能去补数据而不是猜。
- 正常的含节假日窗口**不被误挡**（A 股一年十来个长假，误挡等于不可用）。
- run 记录钉住数据指纹，"这两个 run 可比吗"事后可查。

**Non-Goals:**
- 不自动补数：补数会花上游成本，DAAS 侧已有显式 `fill_gaps`；本仓替用户决定"我要为这次回测抓 730 天数据"是越界。
- 不在本仓重新实现覆盖度判定（唯一真源在 DAAS）。
- 不改撮合/统计口径。
- 不为模拟 provider 伪造覆盖度。

## Decisions

### D1. 覆盖度是 provider 的**可选能力**，不是协议新增的必选项

**选择**：新增 `get_bars_with_coverage(symbol, start, end) -> dict`，只有 `FdOpenDataMarketDataProvider` 实现。门禁用 `hasattr` 探测；探测不到就**跳过门禁并如实记录"未能检查"**，而不是当作通过、也不是当作失败。

**理由**：`MarketDataProvider` 协议有三个实现（simulated / polygon / fd_open_data），后两者中 simulated 的序列是合成的、polygon 走 simulated 兜底。把覆盖度加进协议意味着每个实现都要造一个值，而**给模拟 provider 伪造"完整覆盖"就是在教它说谎**——将来有人把模拟数据接进回测，门禁会亮绿灯。

**备选**：协议加必选方法（诚实但逼所有人造值）；门禁只看 `isinstance(provider, FdOpenData...)`（类型耦合，且伪装/装饰器会绕过）。否。

**关键后果**：门禁是**尽力而为**的。一个不知道自己不完整的 provider 无法被拦。这是刻意的取舍——门禁的价值在于拦住**知道**自己缺数据的路径，而不是假装能拦住所有路径。`run.params` 里记 `coverage_checked: false` 让"这次没检查"本身可见。

### D2. 检查点放在取数之后、回放之前，且有覆盖度的 provider 会**先探明一次**

**选择**：`_replay_single_symbol` / `_replay_universe`（即 `routes/strategies.py` 里取数处）取完 bars 后调用 `get_bars_with_coverage`；provider 内部先做一次 `fill_gaps` 的**探明**（不是补数：只问缺口、记下"确实没有"的区间，已有数据不改），再返回 `missing` / `market_closed`。

**理由**：不做探明的话，任何含春节/国庆的窗口都会被判"missing"而拒绝——A 股一年十来个长假，等于回测功能不可用。探明的成本是每个窗口几条上游请求（实测两年窗口 6 条），换来"休市不算缺失"的正确判定。

**备选**：要求用户先跑 `prewarm-prices`（把正确性变成用户的运维负担，且预热只覆盖活跃池）。否。

**注意**：探明**不写价格数据**（不补数），只记录"该区间问过、确实没有"。这两件事在 DAAS 侧已经是分开的（`fill_gaps` 的 `_record_empty` 只写空区间记录，不写观测）。

### D3. 拒绝语义：`FAILED` + 点名标的与区间

**选择**：`missing` 非空 → run 转 `FAILED`，`error` 形如
`incomplete price data for AAPL: 2025-03-01..2025-03-31 (未覆盖 31 天); other gaps: ...`
权重轨：任一标的有 `missing` → 整体 FAILED 并点名该标的（不静默用其余标的算）。

**理由**：与既有「no data fails the run」并列而非替代——两者都是"数据不足无法回测"，只是粒度不同。点名区间是因为**可操作性**：用户要知道补哪一段。

**权重轨为何整体拒绝**：组合曲线按不完整价格估值会连带污染所有标的的权重快照；部分 universe 算出的组合收益率没有意义。

### D4. 指纹写进 `run.params`，不新增列

**选择**：`run.params["data_fingerprints"] = {symbol: fingerprint}`、`run.params["coverage"] = {symbol: {missing: [...], market_closed: n}}`、`run.params["coverage_checked"] = bool`。

**理由**：`params` 是既有 JSONB，语义就是"这次运行的输入参数与数据"。新增列要走迁移，而这三项是**诊断信息**不是查询维度（没人会 `WHERE fingerprint = ...`）。

**可测后果**：同一窗口两次回测指纹相同；数据变了指纹变——这才让"两个 run 可比吗"成为可回答的问题。

### D5. `market_closed` 要进 run 记录，但不阻塞

**选择**：把 `market_closed` 的段数与总天数写进 `run.params["coverage"]`，在 run 详情里可见。

**理由**：一个 730 天窗口里有 60 天休市是**正常**的，但如果某标的报了 400 天休市，那多半是停牌——信息有用，但不该拦。记录而不阻塞，让人自己判断。

## Risks / Trade-offs

- **[门禁是尽力而为的（D1）]** → 一个不知道自己缺数据的 provider 拦不住。缓解：`coverage_checked` 显式记录"没检查"，让遗漏可见而非隐形。
- **[探明会给首次回测加几条上游请求]** → 实测两年窗口 6 条；相对于一次回测本身可忽略。且探明结果被记录，后续读取不再问。
- **[模拟 provider 路径完全不变 → 该路径的测试无法验证门禁]** → 门禁测试必须用「实现了覆盖度能力的假 provider」，不能靠模拟 provider 通过来"证明"门禁存在。这是本仓纪律"验证必须对准坏的那个本身"的直接应用。
- **[停牌被算作 market_closed 而放行]** → 停牌与休市在数据上不可区分（都是"该日无价"）。这是接受的：回测按已有 bar 回放，停牌日的估值由既有"缺 bar 用最近收盘"规则处理（`strategy-weight-mode` D4），不会伪造成交。
- **[探明失败（上游不可达）]** → `fill_failed=true` 时 `missing` 保持非空 → 回测被拒。这是对的：**证明不了完整，就不该当完整**。

## Migration Plan

1. provider 加 `get_bars_with_coverage`（真实实现 + 探明）。
2. 门禁点接入（单标的 + 权重轨）。
3. `run.params` 记录指纹/覆盖度。
4. 回滚：删掉 `get_bars_with_coverage` 即回到今天的行为（`hasattr` 探测不到就跳过），无数据迁移。

### D6. 探明有请求预算上限，超限直接判不完整

**选择**：一次检查最多探明 `MAX_PROBE_INTERVALS = 20` 段缺口；超限的缺口**不探明**，保持 `missing`（即回测被拒），错误里说明"缺口过多（N 段），需先补数"。

**理由（实测支撑）**：正常 A 股两年窗口的缺口是 **7 段**（2024-10-17→2026-10-16 的真实序列实测：春节 9 天、劳动节 6 天、国庆 9 天、元旦 5 天、春节 11 天、劳动节 6 天、国庆 8 天）。上限 20 段留了近 3 倍余量：正常标的永不触限，而一个停牌频繁或数据破碎的标的（50 段）会触限并被拒——那正是该拒绝的情形。

**为什么不是"无限探明"**：探明要花上游请求。一个碎成 50 段的序列意味着 50 次往返，比拒绝更糟：用户等很久，最后仍可能因 `fill_failed` 被拒。**先拒绝并说明原因，比先烧掉 50 次请求再拒绝好。**

**为什么不是"不设上限"而是"超限即判缺失"**：上限的作用是区分"正常休市"与"数据破碎"，而不是节省请求。超限的语义是"这不像是正常休市"，所以判缺失是对的。

## Open Questions

- 是否需要在 web 面板展示 `market_closed` 占比——先记进 API，面板按需再加（`run.params.coverage` 已含数据，加展示不需要改后端）。
