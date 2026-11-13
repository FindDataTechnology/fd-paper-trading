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
- [ ] 3.3 基准端到端（**仍开放，属结构性挂账**，非遗漏）：**结构性挂账**（如实不伪造）——指数零观测 + 抓取政策冻结，8.3 保持挂账；种子落地后本项在 ③ 或数据线 change 补收口。触发条件记 `reports/astock-spike-index-data.md` 结论段
- [x] 3.4 基准缺失报缺：provider 对无数据指数返回整窗 missing（冒烟第 8 项 rows=0 实证）；gate 拒绝点名缺失区间（spec 既有纪律 + 单测）

## 4. 部署与上线

- [x] 4.1 fd-infra-deploy 生产清单 env 注入（secret 引用，只读权限），镜像与仓库内容经查不含连接串 —— **2026-11-14 实测已到位**：`all-services/prod/fd-paper-trading.yaml` 里 `MARKET_DATA_SOURCE_ID: astock_daily` 是显式 env，凭据走 `secretRef fd-paper-trading-env`（不写值）；生产 pod 内实读 `MARKET_DATA_SOURCE_ID=astock_daily`、`ASTOCK_DSN` 存在（只验存在、不打印值）。同期把服务滚到了含止损修复的片（`sha-d274895`，infra 仓 `2612689`）
- [x] 4.2 生产真库冒烟（生产 pod 内全链）—— **2026-11-14 在生产 pod 内跑通，ALL PASS（16 项）**。镜像不带 `scripts/`（`/app/scripts` 不存在），所以把 `scripts/smoke_astock_provider.py` 与同目录的 `_evidence_source.py` 拷到 pod 的 `/tmp/astock-smoke`，用 pod 自己的 `uv run python` 跑（跑完删掉临时目录）。关键读数：`600036` 42 根真 OHLC；`sources seen raw: ['akshare','astock_daily']`（钉源有效）；`adjusted=qfq, declared=qfq`（口径诚实，且本轮修掉的「取不到时报 unknown」在正常路径上不变）；`coverage: 79 bars, missing=0, closed=19`；`730d … 0.9s bars=473 missing=0`；陈旧标的全窗报缺 `bars=0 missing=[{start:2025-01-01,end:2026-07-10}]`；指数实体零观测（spike 结论仍成立）
- [x] 4.3 生产默认 provider 切换决策 —— **2026-11-14 按现状收口（记录既有事实，不是新裁决）**：清单里 `MARKET_DATA_SOURCE_ID` 是**显式** `astock_daily`，所以「默认」这个问题在产线已不存在——它从不走空值那条 simulated 路。原建议（保留 fd-open-data 默认、astock 显式选择）在这条服务上表现为「显式钉 astock + 基金 NAV 面仍由 `FD_OPEN_DATA_*` 三个 concept env 承载」；spike 报告的停更边界不变（指数零观测，已在 4.2 冒烟里再次实测 rows=0）
- [x] 4.4 文档：README provider 家族段 + `.env.example` 增补 ——**2026-11-13 完成**（`8d39201` README「Real market data」重写为注册表口径；`d75ec1a` `.env.example` 删掉启动即失败的旧变量与两个死配置；判据 `tests/unit/test_env_example_is_executable.py` 覆盖这三份文档）

## 5. 收尾

- [x] 5.1 全量测试套件通过（含断网单测同结果纪律）—— **2026-11-13 复核**：`pytest tests` 712 passed（含 `tests/e2e`，在 CI backend job 覆盖内）
- [x] 5.2 `openspec validate astock-price-channel --strict` 通过后归档 —— **已归档**（本 change 在本目录下）。注：4.3「生产默认 provider 切换决策」仍是开放项，但归档这一事实不依赖它——归档的是规格修订，不是上线动作。

---

## 2026-11-13 复核说明（不改原判据，只标状态）

| 项 | 状态 | 依据 |
|---|---|---|
| 3.3 基准端到端 | **开放**（结构性挂账） | 指数零观测 + 抓取政策冻结（用户 2026-09-18 停爬令）；种子落地后由数据线 change 收口 |
| 4.1 生产清单 env 注入 | **已完成**（2026-11-14 实测） | 清单显式 `MARKET_DATA_SOURCE_ID=astock_daily` + secretRef；pod 内实读确认（只验存在） |
| 4.2 生产真库冒烟 | **已完成**（2026-11-14 pod 内 ALL PASS 16 项） | 脚本拷进 pod 跑，读数见 4.2 正文 |
| 4.3 生产默认 provider 决策 | **已收口**（2026-11-14） | 清单显式钉 `astock_daily`，产线不存在「空值即 simulated」那条路；这是现状记录而非新裁决 |
| 4.4 文档 | 已完成 | `8d39201` + `d75ec1a`（本仓 README/.env.example/CLAUDE.md 已改为注册表口径） |
| 5.1 全量测试 | 已完成 | 2026-11-13：712 passed |
| 5.2 归档 | 已完成 | 本目录 |

四项开放项都是**需要用户决策或依赖生产动作**的，不由代码侧补齐；
刻意保留 `[ ]`，以免把"等拍板"写成"已完成"。
