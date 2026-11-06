## 1. Provider 实现

- [x] 1.1 `AstockMarketDataProvider` 骨架：async 只读会话管理（DSN 来自配置）、`get_daily_bars` 单 SQL 透视读联邦视图（`source='astock_daily'` 钉死，OHLC 概念 233/234/235/236 + 量额）、时间粒度非 `1d` 显式拒绝 —— `src/fd_paper_trading/marketdata/astock.py`；真库冒烟 242 根 K 线（reports/runner-smoke.txt）
- [x] 1.2 `get_quote` / `get_latest_close`：最新收盘 + 半价差推导（5bps，与 fd-open-data 同式），无数据返回 `None`（gap 非 error）—— 停更数据上实证 quote=None（冒烟第 5 项，诚实报缺）
- [x] 1.3 `get_instrument` / `search_instruments`：实体表查 code/name，venue 段位映射 XSHG/XSHE，`600036` 与 `600036.SH` 双写法规范化；未解析返回 `None` —— 真库 "招商"/"000333" 搜索命中（冒烟第 4 项）
- [x] 1.4 `get_bars_with_coverage`：覆盖度契约（missing/market_closed/fill_failed/fingerprint）+ `adjusted: "forward"`/`source` 增量键。日历判定实施修订为**参照篮**（D4 回写）：全市场日历扫描实测 >200s 不可用，5 大盘参照篮实测 730d 全窗 2.4s；周末区间算术判定；篮子覆盖上限之后 fail-closed（600519 截断实证整窗报缺）
- [x] 1.5 配置接线：`MARKET_DATA_PROVIDER=astock` 选择、`ASTOCK_DSN`/`ASTOCK_SOURCE`/`ASTOCK_CALENDAR_REFS` 配置项、未配置凭据时选择该 provider → 明确启动错误（不静默顶替 simulated）—— 单测覆盖
- [x] 1.6 单测 26 测：钉源（多源混入只取 astock_daily）、gap 语义、双写法、非 1d 拒绝、coverage 字段、日历失败 fail-closed、空序列不伪装休市、上限后不可证、provider 选择报错/懒连接

## 2. 消费方接线与限额回归

- [x] 2.1 instrument 搜索（web + MCP）出 A 股实体；下单前置 `get_instrument` 解析失败即拒单 —— 搜索为 provider 方法（routes 既有透传）；非法符号 `get_instrument`→None→既有拒单语义（test_get_instrument_syntactic + 订单既有缺报价拒单回归）
- [x] 2.2 回测/coverage gate 消费新 provider：部分覆盖 FAILED 点名、休市放行、缺口预算 —— gate 为 provider 无关纯决策（_gate_coverage 既有单测 + astock coverage 返回契约同键）；真实 A 股形态在真库冒烟第 3/6/7 项实证（missing=0/closed=19、730d、陈旧报缺）
- [x] 2.3 run `params` 指纹记录增复权口径字段；同策略两次 run 口径可区分 —— `_coverage_params` 透传 `adjusted`/`source`（未声明不臆造键）；`test_params_record_adjustment_caliber_when_declared`
- [x] 2.4 group 限额回归：`max_backtest_days` 在路由层 provider 无关（既有回归测试不改）；quote lookback 30d 与 fd-open-data 同式（单测）。**注**：spec delta 的 symbol-limit 场景原稿基于 `per_user_symbol_limit` 假前提（该配置全仓无执行点，grep 实证）——已按事实修订为数据预算需求；孤儿配置本身作为既有挂账记 spike 报告，不属本 change
- [x] 2.5 live runner 冒烟：astock 真实序列喂既有模板评估器出信号（2 signal bars/242 bars）、`_first_new_bar`/`_latest_close`/处理日锚定行为不变 —— `scripts/runner_smoke_astock.py`，输出落 reports/runner-smoke.txt

## 3. 指数日线与基准端到端（spike 定论后改述）

- [x] 3.1 spike：两候选实证 —— **候选 B（分片库指数源）死路**：指数实体（9676/10263）已注册但全库零观测（概念 288/289 亦零）；**候选 A 卡政策**（2026-10-07 服务端不重开价格抓取）。证据 `reports/astock-spike-index-data.md`
- [x] 3.2 （原"落地指数日线供数"→spike 定论）provider 侧就绪：`_resolve_entity` stock→index fall back 已实现并有单测（`test_index_symbol_without_rows_is_gap`），指数种子一旦落地即自动可用、不改接口；**种子本身 = 数据线决策，交接挂账**（不在本 change 只读授权内，本仓不写平台库）
- [ ] 3.3 基准端到端：**结构性挂账**（如实不伪造）——指数零观测 + 抓取政策冻结，8.3 保持挂账；种子落地后本项在 ③ 或数据线 change 补收口。触发条件记 `reports/astock-spike-index-data.md` 结论段
- [x] 3.4 基准缺失报缺：provider 对无数据指数返回整窗 missing（冒烟第 8 项 rows=0 实证）；gate 拒绝点名缺失区间（spec 既有纪律 + 单测）

## 4. 部署与上线

- [ ] 4.1 fd-infra-deploy 生产清单 env 注入（secret 引用，只读权限），镜像与仓库内容经查不含连接串 —— **待用户确认生产滚动**（provider 默认不变，仅加 env）
- [ ] 4.2 生产真库冒烟（生产 pod 内全链）—— 依赖 4.1；**注**：开发机真库只读冒烟已全部通过（=生产同一库同一读路）
- [ ] 4.3 生产默认 provider 切换决策 —— 荐：保留 fd-open-data 默认（基金 NAV 面在产），astock 显式选择；live 面受停更边界制约（见 spike 报告），切换与否属上线拍板
- [ ] 4.4 文档：README provider 家族段 + `.env.example` 增补 —— 本轮执行

## 5. 收尾

- [ ] 5.1 全量测试套件通过（含断网单测同结果纪律）—— 本轮执行
- [ ] 5.2 `openspec validate astock-price-channel --strict` 通过后归档 —— 3.3/4.x 未勾项按 spec 原文为"when available"条件语义与上线动作，不构成 validate 阻塞；归档时点由用户按 4.3 决策定
