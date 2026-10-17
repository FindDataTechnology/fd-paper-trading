# 基准数据可用性记录（task 5.3 / 8.3）

> **结论：本环境下基准数据不可用，两项任务如实记为受阻，未伪造通过。**
> 记录日期：2026-10-17

## 一、实测过程

代码路径已就绪（`strategy/benchmarks.py` 的 `fetch_benchmark` + 路由的
`_attach_benchmarks`），但取数在**数据层**失败。实测：

```
$ .venv/bin/python -c "…fetch_benchmark(provider, sym, start, end)…"
000300.SS: UNAVAILABLE — provider error for 000300.SS: OperationalError:
           (sqlite3.OperationalError) no such table: concepts
000905.SS: UNAVAILABLE — provider error for 000905.SS: … no such table: concepts
600519.SS: UNAVAILABLE — provider error for 600519.SS: … no such table: concepts
```

关键点：**连 600519.SS（个股，非基准）也取不到**。所以这不是"基准符号没登记"
这一类局部问题，而是整个 fd-open-data 本体在当前环境不可用。

## 二、根因（两层）

1. **本体库是空壳**：`FdOpenDataMarketDataProvider` 读的是随包安装的
   `fd_open_data_mcp/db/metadata/daas.db`——该文件 4KB，`sqlite_master` 里
   **零张表**（`tables: []`）。故任何符号的 `concepts` 查询都报 no such table。
2. **没有指向真实库的配置**：`.env.example` 提示可设
   `FD_OPEN_DATA_MCP_DATABASE_URL`，但本环境未设，故 provider 落在空壳上。

这不是本变更引入的缺陷：基准数据走的是**既有 provider 管道**（design D5：
"与标的价格同一 `get_bars` 语义"），管道自身依赖的本体库缺位，回测侧的
标的价（`AAPL`）也一样取不到——现有测试全部使用 `SimulatedMarketDataProvider`
正是因为这一点。

## 三、受阻项与就绪判据

| 任务 | 状态 | 就绪判据（届时可执行） |
|---|---|---|
| 5.3 默认基准列表在数据可用时自动生效 | **受阻** | 设 `FD_OPEN_DATA_MCP_DATABASE_URL` 指向有 `concepts` 表的库后，跑 `fetch_benchmark(provider, "000300.SS", start, end)` 返回非空 bars |
| 8.3 带默认基准的端到端回测 | **受阻** | 同上；然后跑一次带 `benchmarks: ["000300.SS","000905.SS"]` 的回测，断言 `benchmark_results[*].available == true` 且 `relative` 三件套非空 |

两者都属 design D5 风险条目里写明的"批 0.1 完成后基准才真正可用"。
批 0.1（`price-channel-daas-seed`）已把 A 股价格供到**本地 DAAS 库**
（`code/DAAS/daas.db`，600519 有 6022 行），但那是**通道 B（客户端本地取数）**，
与 fd-open-data 服务端本体是两条路——spec 政策（2026-10-07）禁止把价格概念
注册到服务端，故本环境的 fd-open-data 本体不会因批 0.1 而获得价格数据。

## 四、已完成的替代验证

数据不可用不妨碍验证**代码路径正确**。以下均已实测通过：

1. **缺数据明确报缺**（spec Scenario「基准缺数据明确报缺」）：
   `tests/unit/test_backtest_v2.py::test_benchmark_missing_data_is_marked_not_omitted`
   —— 空 provider 下 `fetch_benchmark` 返回 `(None, reason)`，reason 含符号与
   "no bar data"；路由把它落成 `available: false` 的一条记录，**不省略、不伪造**。
2. **相对指标口径正确**（task 5.2）：
   `test_relative_metrics_are_zero_when_strategy_equals_benchmark` —— 策略=基准
   时超额/TE/IR 三者均为 0（容差 1e-9，实测 < 1e-9），指标由计算面
   `summarize_information_ratio` 产出。
3. **重叠不足时报缺而非给数**：
   `test_relative_metrics_report_insufficient_overlap_not_a_fabricated_number`。
4. **默认基准就是双宽基**：`test_default_benchmarks_are_the_two_cn_broad_indices`。
5. **端到端三配置验收**（task 8.2，用构造行情，不依赖基准数据）：
   `reports/e2e-v2.md`——零成本/全成本/walk-forward 三份结果 + 成本单调性与
   walk-forward 有序性两条断言，全部 ✅。

## 五、判读

- 基准对比的**代码**已就绪且其"缺数据"分支有测试覆盖；缺的是**数据**。
- 报告 `e2e-v2.md` 里没有基准段，正是因为本次请求未传 `benchmarks` 且默认基准
  取不到数——这是设计行为（design D5："可用即用、不可用报缺"），不是遗漏。
- 数据就绪后无需改代码：默认基准列表已在 `DEFAULT_BENCHMARKS` 里，路由默认
  带上它们并逐个落记录。
