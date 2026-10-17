# Contract closed-loop report (task 6.4)

> **本文件由脚本生成，请勿手改** — 重跑 `python3 fd-paper-trading/scripts/e2e_contract_loop.py` 覆盖。
> 手改会在下次重跑时静默丢失，且报告与代码不再可对账。

**链路**：pack 形态的 `TargetWeights` 实例 → 契约层校验 → 引擎消费（spec 校验 + 真实价格回测）→ 可解读的结果。

## 1. 输入：pack 形态的契约实例

```json
{
  "schema_version": "1",
  "as_of": "2000-01-01",
  "universe": [
    "AAPL",
    "MSFT",
    "AMZN"
  ],
  "weights": {
    "AAPL": 0.4,
    "MSFT": 0.3,
    "AMZN": 0.1
  },
  "cash_floor": 0.2,
  "constraints": {
    "max_weight_per_symbol": 0.5
  },
  "evidence": [
    {
      "evidence_id": "ev-1",
      "label": "trailing 6m momentum ranking",
      "value": "AAPL>MSFT>AMZN",
      "unit": null,
      "as_of": "2000-01-01",
      "source": "pack-strategy-lab/momentum"
    }
  ],
  "producer": {
    "pack": "pack-strategy-lab",
    "skill": "portfolio-weights"
  }
}
```

## 2. 契约层校验

- 结论：`valid = True`
- 错误数：0

实例按**发布态**校验（`schema_version` / `producer` / `evidence` 一并管），不是先压成裸映射再看权重——否则实例的声明部分会被静默忽略。

## 3. 引擎消费（权重与 cash_floor 都穿过来了）

| 字段 | 契约实例 | 引擎 spec | 一致 |
|---|---|---|---|
| weights.AAPL | 0.4 | 0.4 | 是 |
| weights.AMZN | 0.1 | 0.1 | 是 |
| weights.MSFT | 0.3 | 0.3 | 是 |
| cash_floor | 0.2 | 0.2 | 是 |

`cash_floor` 是契约独有的字段（裸映射表达不了），所以它穿过来这件事本身就是"实例被当作实例消费"的证据。

## 4. 回测结果

| 指标 | 值 |
|---|---|
| `total_return` | 0.076487 |
| `max_drawdown` | 0.130459 |
| `sharpe` | 0.962478 |
| `trade_count` | 16 |
| `final_equity` | 215297.3945 |
| `rebalance_count` | 6 |
| `scaled_instruction_count` | 0 |

窗口：2026-01-29 → 2026-07-22，共 120 个权益点，6 次调仓。

## 5. 每期快照（目标来自 pack 的实例）

### 2026-01-29

- 目标（pack 给的）：AAPL=0.4, AMZN=0.1, MSFT=0.3
- 实际（引擎做到）：AAPL=0.399043, AMZN=0.099109, MSFT=0.299115
- 目标现金下限：0.2

| 标的 | 方向 | 股数 | 价格 | 缩量 |
|---|---|---|---|---|
| AAPL | BUY | 309 | 258.2799987792969 | 否 |
| AMZN | BUY | 82 | 241.72999572753906 | 否 |
| MSFT | BUY | 138 | 433.5 | 否 |

### 2026-02-27

- 目标（pack 给的）：AAPL=0.4, AMZN=0.1, MSFT=0.3
- 实际（引擎做到）：AAPL=0.40119, AMZN=0.099795, MSFT=0.298212
- 目标现金下限：0.2

| 标的 | 方向 | 股数 | 价格 | 缩量 |
|---|---|---|---|---|
| AAPL | SELL | 15 | 264.17999267578125 | 否 |
| AMZN | BUY | 10 | 210.0 | 否 |
| MSFT | BUY | 9 | 392.739990234375 | 否 |

### 2026-03-27

- 目标（pack 给的）：AAPL=0.4, AMZN=0.1, MSFT=0.3
- 实际（引擎做到）：AAPL=0.400135, AMZN=0.100321, MSFT=0.298599
- 目标现金下限：0.2

| 标的 | 方向 | 股数 | 价格 | 缩量 |
|---|---|---|---|---|
| MSFT | BUY | 6 | 356.7699890136719 | 否 |

### 2026-04-27

- 目标（pack 给的）：AAPL=0.4, AMZN=0.1, MSFT=0.3
- 实际（引擎做到）：AAPL=0.399258, AMZN=0.100906, MSFT=0.301317
- 目标现金下限：0.2

| 标的 | 方向 | 股数 | 价格 | 缩量 |
|---|---|---|---|---|
| AMZN | SELL | 13 | 261.1199951171875 | 否 |
| MSFT | SELL | 8 | 424.82000732421875 | 否 |
| AAPL | BUY | 11 | 267.6099853515625 | 否 |

### 2026-05-26

- 目标（pack 给的）：AAPL=0.4, AMZN=0.1, MSFT=0.3
- 实际（引擎做到）：AAPL=0.401288, AMZN=0.099527, MSFT=0.29867
- 目标现金下限：0.2

| 标的 | 方向 | 股数 | 价格 | 缩量 |
|---|---|---|---|---|
| AAPL | SELL | 24 | 308.3299865722656 | 否 |
| AMZN | BUY | 2 | 265.2900085449219 | 否 |
| MSFT | BUY | 10 | 416.0299987792969 | 否 |

### 2026-06-24

- 目标（pack 给的）：AAPL=0.4, AMZN=0.1, MSFT=0.3
- 实际（引擎做到）：AAPL=0.400442, AMZN=0.098936, MSFT=0.299602
- 目标现金下限：0.2

| 标的 | 方向 | 股数 | 价格 | 缩量 |
|---|---|---|---|---|
| AAPL | SELL | 6 | 293.0799865722656 | 否 |
| AMZN | BUY | 4 | 234.27000427246094 | 否 |
| MSFT | BUY | 10 | 365.4599914550781 | 否 |

## 6. 结论

- pack 产出的契约实例**直接**进入引擎：没有转写、没有重算权重、没有归一化。
- 权重合法性由契约层判定，引擎不持有第二套规则（改契约即改引擎行为，由 `tests/unit/test_weight_contract_gate_selfcheck.py` 打桩钉住）。
- `cash_floor` 作为契约独有字段被引擎消费，说明实例是作为实例处理的。
- 结果可解读：每期目标 vs 实际并列，取整残差留痕。
