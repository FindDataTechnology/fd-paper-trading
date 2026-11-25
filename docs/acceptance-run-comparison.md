# 验收记录：回测 → 纸面运行 → 停 → 比较（`add-paper-run-metrics` 6.1）

> 真实行情（`finddata/DAAS/daas.db` 四只美股最近 200 根日线）+ 真 fd-quant 计算面
> （`127.0.0.1:8799`，本机真起 `fd_quant.compute.server`）+ 真 `PaperExecutionService`。
> 命令见 §4。日期 2026-11-25。

## 1. 全链跑通

| 步 | 结果 |
|---|---|
| 回测（同策略，真实窗口） | `COMPLETED`，`stats` 有值 |
| 起纸面运行（直接建 run 行 + 两次 `_tick_once`，同一驱动避免双驱动重复成交） | 两次调仓各成一笔 |
| 停 | `STOPPED` |
| 比较 | 见 §2 两种形态 |

## 2. 两种形态都产出（任务要求的正是这两种）

### 形态 A：回测 vs 纸面 —— **不可比**（`comparable: false`，`stats: null`）

```
reasons:
  - initial_cash            | initial_cash differs (100000 vs None)
  - simulation_profile.cost | cost differs ({'mode': 'none'} vs {'mode': 'modeled',
                              'commission_rate': '0.00025', 'stamp_tax_rate': '0.0005', ...})
  - stats                   | no headline statistics for the other run
```

三条原因各自指名一个**真实差异**：回测按请求给了初始现金、纸面按账户余额（不记 `initial_cash`
参数）；回测这一次未开成本、纸面按执行服务的成本模型扣费。**不可比时返回 `stats: null`**，
不给一张全 null 的差值表——这正是 D7「不可比是数据，不是省略」的口径。

### 形态 B：两次独立纸面运行（不同账户、同策略同行情）—— **可比**（`comparable: true`）

```
diff: {total_return: 0.0, max_drawdown: 0.0, trade_count: 0.0, final_equity: 0.0}
self.final_equity = 97704.8245   other.final_equity = 97704.8245
```

同一实验跑两遍、差值逐项为零，证明「可比」这一臂**不是空壳**：它真的在比较，且比较结果可复现。

## 3. 附带发现（不阻塞本任务）

- 回测**依赖 fd-quant compute plane**（no-lookahead 断言）：不起计算面时回测直接 `FAILED`
  （`compute plane unavailable ... Connection refused`）。这是设计 D2 的「与计算面同数」
  要求，不是缺陷；但要跑 6.1 必须先起 `fd_quant.compute.server`（见 §4 第 1 行）。
- 「回测 vs 纸面」在当前实现下**永远不可比**，因为纸面 run 的 `params` 不记 `initial_cash`。
  这是否该改，取决于产品是否想支持跨模式比较；本 change 未要求，故记录不改。

## 4. 复现命令

```bash
# 1) 起真计算面（fd-quant 仓内）
(cd ~/finddata/fd-quant && .venv/bin/python -m fd_quant.compute.server --http 8799 &)

# 2) 跑验收（脚本用到 fd-paper-trading 的 e2e 取数与驱动）
FD_QUANT_COMPUTE_ENDPOINT=http://127.0.0.1:8799/mcp \
FD_PRICE_DB=~/finddata/DAAS/daas.db \
  fd-paper-trading/.venv/bin/python /tmp/compare_drill.py
```