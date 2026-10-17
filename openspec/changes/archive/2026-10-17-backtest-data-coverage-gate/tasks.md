## 1. Provider 覆盖度能力

- [x] 1.1 `FdOpenDataMarketDataProvider` 新增 `get_bars_with_coverage(symbol, start, end) -> dict`，返回 `bars` / `missing` / `market_closed` / `fill_failed` / `fingerprint`。验证：对已补数窗口返回 `missing=[]`；对零数据窗口返回全窗 `missing`
- [x] 1.2 覆盖度判定**复用 DAAS 的 `fill_gaps`**，不在本仓重写规则。验证：代码中无独立的"缺口计算"实现；`missing`/`market_closed` 直接来自 DAAS 返回
- [x] 1.3 探明（probe）会询问缺口但不写价格数据。验证：对一个只有部分数据的窗口调用后，`semantic_observations` 行数不变，`price_empty_intervals` 有新增
- [x] 1.4 探明有段数上限（20 段），超限的缺口保持 `missing`。验证：构造 30 段缺口的夹具，断言 `missing` 非空且错误说明"缺口过多"
- [x] 1.5 未实现该能力的 provider 不受影响。验证：`SimulatedMarketDataProvider` 无该方法；既有测试全绿

## 2. 单标的门禁

- [x] 2.1 `_replay_single_symbol` 取数后检查覆盖度，`missing` 非空则 `FAILED` 并点名标的与至少一段区间。验证：部分覆盖夹具 → run FAILED，`error` 含标的代码与 `YYYY-MM-DD..YYYY-MM-DD`
- [x] 2.2 `market_closed` 非空不拒绝。验证：只有休市缺口的窗口 → run COMPLETED
- [x] 2.3 `fill_failed=true` 时缺口仍算 `missing` → 拒绝。验证：上游失败的夹具 → run FAILED
- [x] 2.4 完全无数据的既有行为不变。验证：既有 `test_backtest_no_data_fails` 仍绿
- [x] 2.5 provider 无覆盖度能力时跳过门禁并记录。验证：模拟 provider 路径 run COMPLETED，`params.coverage_checked` 为 false

## 3. 权重轨门禁

- [x] 3.1 `universe` 逐标的检查，任一 `missing` 非空 → 整体 `FAILED` 并点名该标的。验证：三标的中一个部分覆盖 → FAILED，error 含该标的
- [x] 3.2 全部标的仅休市缺口 → 照常完成。验证：三标的均只有节假日缺口 → COMPLETED 且有 `weight_snapshots`
- [x] 3.3 逐标的检查不因一个标的失败而跳过其余检查（错误信息应列出所有不完整标的）。验证：两个标的不完整 → error 同时含两者

## 4. run 记录钉指纹

- [x] 4.1 `run.params["data_fingerprints"]` 记录每标的指纹。验证：完成后读 run，`params` 含该键且值非空
- [x] 4.2 `run.params["coverage"]` 记录每标的的缺失段数与休市段数。验证：字段齐全且与实际缺口语义一致
- [x] 4.3 `run.params["coverage_checked"]` 如实记录是否检查。验证：真实 provider → true；模拟 provider → false
- [x] 4.4 同一窗口两次回测指纹相同；数据变化后指纹变化。验证：连跑两次断言相同；补一段数据后再跑断言不同

## 5. 反向自检与回归

- [x] 5.1 **反向自检**：把门禁改坏（例如让 `missing` 恒为空）时，2.1/3.1 的用例必须失败。验证：临时改坏实现跑测试确认红，再改回
- [x] 5.2 既有 377 项后端测试零新增失败。验证：全量对比改动前后失败清单一致
- [x] 5.3 模拟 provider 路径的行为与统计字段完全不变。验证：既有回测统计断言全绿

## 6. 端到端

- [x] 6.1 真实数据端到端：对已补数的 `000001` 跑 730 天单标的回测 → COMPLETED，`params` 含指纹与覆盖度摘要。验证：报告落 `reports/`，含指纹与休市段数
- [x] 6.2 真实数据拒绝路径：构造一个部分覆盖的窗口（例如把缓存截到只有 60 天），回测被拒并点名区间。验证：报告含实际 `error` 文本
- [x] 6.3 跨仓联调复核：确认 `astock-price-on-demand` 的 `missing`/`market_closed` 语义与本仓门禁一致。验证：用同一份数据两侧各自判定，结论一致
