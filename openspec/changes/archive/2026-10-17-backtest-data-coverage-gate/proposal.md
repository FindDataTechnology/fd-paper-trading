## Why

`astock-price-on-demand`（`fd-open-data-mcp`，33/33 已实施）现在能如实报告一个价格窗口的覆盖度：`gaps`（仍缺失的区间）、`fill_attempted`、`fill_failed`、`fingerprint`（内容哈希）。实测证据（`reports/e2e-price-fill.md`）：`000001` 冷启动补出 485 点，指纹三次一致；`reports/consumer-discipline-status.md` 记录了本仓消费侧的现状。

**本仓今天只拦一种情形：完全无数据。** 实读 `api/routes/strategies.py`：

```python
bars = await provider.get_bars(spec.symbol, "1d", days)
if not bars:
    raise EvaluationError(f"no bar data for symbol ... in {start}..{end}")
```

权重轨同理（`if not any(bars_by_symbol.values())`）。**没有任何部分覆盖检查**。后果是具体的、不是假设的：

- 一个标的在 730 天窗口里只有 12 天数据 → 回测按 12 天算出 `total_return`，结果**看起来像真的**，`stats` 齐全、权益曲线完整。
- 中央库里的 `price.close` 正是这种形态（3,533 只、2026-06-09→09-18、每只中位数 44 行）。一旦本仓接上该数据源，这份"碎数据"会立刻变成静默偏短的收益率。
- 权重轨更糟：`universe` 里任一标的部分覆盖，组合曲线按不完整的价格估值，而 `weight_snapshots` 会把它记录成"引擎做到的实际权重"。

这与本仓既有纪律直接冲突。同一节里已经写着"静默忽略一个显式开启的成本模型，等于给出一份零成本的结果而用户以为含费——那正是本仓纪律禁止的沉默谎言"。**在 12 天上算 730 天的收益率是同一种谎言。**

## What Changes

- **provider 暴露覆盖度（可选能力）**：新增一个可选方法 `get_bars_with_coverage(symbol, start, end) -> (bars, coverage)`，由真实数据源 `FdOpenDataMarketDataProvider` 实现（内部走 `read_range` + 覆盖度判定；不触发补数，补数是显式动作）。**不改** `MarketDataProvider` 协议本身，也不改既有 `get_bars`/`get_daily_bars` 的签名——模拟 provider 没有这个能力时不参与门禁。
- **回测门禁**：取数后若 provider 报告该窗口**部分覆盖**（`gaps` 非空且不是"已探明无数据"的休市区间），run SHALL 转 `FAILED`，错误点名缺失区间与标的。判断依据是缺口是否落在**可交易日的缺失**上，而不是"点数为 0"。
- **run 记录钉住数据指纹**：`strategy_runs.params` 记录每个标的的 `fingerprint` 与 `gaps`。这样即使上游日后修订前复权序列，事后也能发现"这两个 run 不可比"。
- **权重轨逐标的不完整即拒绝**：`universe` 里任一标的不完整 → 整个 run FAILED 并点名该标的（不静默用其余标的算下去）。
- **不改变既有行为**：完全无数据仍按既有要求 FAILED；模拟 provider（无覆盖度能力）路径完全不变，既有 377 项测试不受影响。

## Capabilities

### New Capabilities

- `backtest-data-coverage`: 回测取数的覆盖度门禁——provider 的可选覆盖度能力、部分覆盖的拒绝语义（点名标的与缺失区间）、已探明无处数据的休市区间不算缺失、权重轨逐标的检查、以及 run 记录钉住数据指纹。

### Modified Capabilities

- `strategy-backtest`: 「Backtest run over real daily bars」增加"窗口必须被完整覆盖"的前置；「Backtest failure is explicit」增加部分覆盖这一失败原因（与既有"no data"并列，而不是替代它）。

## Impact

- **代码**：`marketdata/fd_open_data.py`（新增 `get_bars_with_coverage`）、`marketdata/provider.py`（文档说明可选能力，不改协议）、`strategy/backtester.py` 或 `api/routes/strategies.py`（门禁点）、`api/routes/strategies.py`（把指纹写入 `run.params`）。
- **数据**：无 schema 变更（`strategy_runs.params` 是既有 JSONB 列）。
- **依赖**：`fd-open-data-mcp` 的 `fetch/coverage.py` 与 `fetch/gapfill.py`（已实施，33/33）。注意**不自研覆盖度判定**——消费方只消费 `gaps`，规则的唯一住在 DAAS 侧。
- **上游消费方**：web 面板的回测错误展示（已有 `run.error` 通道，无需改动）。
- **不作承诺**：不做自动补数（补数是 DAAS 侧的显式 `fill_gaps`，本仓不替用户决定花上游成本）；不改成交价/撮合/统计口径；不为模拟 provider 伪造覆盖度。
