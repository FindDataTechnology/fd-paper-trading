# 验收：纸面运行调仓决策落账（2026-11-26）

change `wire-paper-runs-to-decision-ledger` 任务 5.1–5.3 的真机验收记录。
环境：production（`paper.finddatatech.cloud`，cheap 集群 fd-prod），镜像
`fd-paper-trading:sha-a8c3bf3`，fd-quant 同轮挂上 `FD_QUANT_CALLER_HMAC_KEY`。

## 前置（任务 4 的集群判据）

```
$ kubectl -n fd-prod exec deploy/fd-paper-trading -- sh -c '
    echo "$FD_QUANT_LEDGER_ENDPOINT"; echo -n "$FD_QUANT_CALLER_HMAC_KEY" | sha256sum | cut -c1-16'
http://fd-quant:8080/mcp
fd2a726d89b9b35f

$ kubectl -n fd-prod exec deploy/fd-quant -- sh -c 'echo -n "$FD_QUANT_CALLER_HMAC_KEY" | sha256sum | cut -c1-16'
fd2a726d89b9b35f
```

两侧同值（同一 secret `fd-quant-line-token` 的两个挂载点），不打印值只比哈希。

## 5.1 落进用户自己的库 + 隔离

造数：用户 `ledger-walk1126@findmail.com` 起一条权重轨纸面运行
（`600000`/`600036` 各 0.5，`every_n_bars=1`），运行 id
`3f584220-04b5-4784-a34b-858f4981611f`。

- 首 tick 落账（fd-quant 侧直查）：
  `('portfolio', '2026-11-26', 'strategy_run:3f584220-…', 'fd-paper-trading', 'strategy-runner')`
  落在 `ledger-walk1126-findmail-com-9f97a271/ledger.db`（caller 派生目录）。
- 以该用户身份 `list_pending_decisions(record_kind="portfolio", as_of=2026-11-26)`：
  返回 `dec-4eb5bbf231dd45ef`，`subject = strategy_run:3f584220-…`。
- 换用户 `ledger-other1126@findmail.com` 同一调用：`items: 0`，库落在
  `ledger-other1126-findmail-com-5b88be93/ledger.db`——**另一份库、查不到**。

同一 tick 的交易**照常完成**：`600000 BUY FILLED 4830 @ 10.36`；`600036` 当日
无 bar 被 SKIP（不落账，符合 design D5）。

## 5.2 结算闭环

对 `dec-4eb5bbf231dd45ef` 调 `settle_portfolio`（两条腿 + `000300.SH` 基准，
6 个交易日窗、`holding_days=5`）：

```
created: True | reason: settled
  raw_return: 0.021681021681021706
  benchmark_return: 0.004888888888888943
  alpha: 0.016792132792132763
  holding_days: 5
  invested_weight: 1.0
  cash_weight: 0.0
```

组合记录能被喂入结算窗序列并算出 alpha——这是 propose「Why」里断掉的那一段。

## 5.3 失败可见 + 不阻断交易

首次 tick 时 fd-quant 握手超时（rolling 冷启动，5s 上限），运行记录：

```
ledger: {failed: 1, created: 0, attempted: 1,
         last_error: "ledger_unreachable: compute plane unreachable at
                      http://fd-quant:8080/mcp (ReadTimeout: timed out)"}
```

运行事件：`error | decision ledger: 1/1 record(s) failed: …`，
`data={date, failed:1, attempted:1, last_error}`。

**注意 at-least-once**：这次超时的请求实际已落库（12:16:28 那行），客户端只是
没等到应答。幂等键 `(caller, record_kind, as_of, subject_key, pack, skill)` 保证
重试不双记——所以「计数说 failed」与「账本里有这行」可以同时为真，这不是缺陷，
是 design D4「账本不可用时交易照常、失败留痕」的必然形态。

## 判据与 spec 字面的两处偏差（如实记录）

1. **5.1 写的 `retrieve_decisions` 查不到组合记录**：该工具只回**已结算、且按
   `d.symbol` 过滤**的单标的记录（SQL 里 JOIN settlements + `d.symbol = ?`）。组合
   记录无 symbol、未结算时也不在结果里。故 5.1 的"能查到/查不到"判据用
   `list_pending_decisions(record_kind="portfolio")` 求值——语义相同（按 caller 隔离），
   且是唯一能看到未结算组合记录的入口。
2. **5.2 写的 `settle_decision`** 只结算单标的记录；组合记录对应的是
   `settle_portfolio`（legs 多腿 + 必填 benchmark_symbol）。验收用后者，产出同样是
   `settled` + alpha。

## 复跑要点

- 运行器每 `STRATEGY_POLL_SECONDS`（默认 1800s）tick 一次；首 tick 在起跑即发生。
- 调仓点需要新 bar；生产 A 股日线水位在 `2026-11-26`，晚上跑活跃运行即命中。
- 手动复跑落账：拿 pod 的 `/app/.venv/bin/python`，用
  `fd_paper_trading.strategy.ledger_identity.caller_header` + `ledger_plane.record_decision`
  （`ledger_plane` 自己不发头，`caller_header` 现签）。
