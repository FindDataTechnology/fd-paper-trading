"""成本模型：A 股费结构（佣金双边 / 印花税卖出单边 / 滑点双边）。

## 默认值为什么是 A 股的数

design D4 写明：默认值取 A 股现实而非 FinRL-X 的美股量级（0.1% 双边）。佣金
0.025%、印花税 0.05% 仅卖出、滑点 5bps——"默认成本"就是回测可信度的下限，
给一个偏低的默认值等于让所有回测都偏乐观。

## 为什么单独成模块而不是塞进 backtester

回测器已经 230 行且职责是"逐 bar 重放"。成本是一组**可独立手算验证**的算术
（成交价怎么调、现金怎么扣），放在这里可以用纯函数逐项对拍；塞进重放循环里
就只能靠"跑一遍看总数对不对"，而总数对不代表每一笔对。

## 单调性（spec 要求"全成本净收益不高于零成本"）

由构造保证：买入时成交价上浮、卖出时下浮、两类费用只从现金扣——三者都只
减少现金或抬高持仓成本，没有反向作用项。`enabled=False` 时所有调整都是
恒等（价格不变、费用为 0），故零成本路径与改动前逐位一致。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping, Optional

#: A 股默认费结构（spec 钉死这些默认值）
DEFAULT_COMMISSION_RATE = Decimal("0.00025")   # 0.025%，买卖双边
DEFAULT_STAMP_TAX_RATE = Decimal("0.0005")     # 0.05%，仅卖出
DEFAULT_SLIPPAGE_BPS = Decimal("5")            # 5bps，买卖双边

BPS = Decimal("10000")


@dataclass(frozen=True)
class CostModel:
    """一次回测的成本配置（不可变——它是会被快照进运行记录的配置）。

    `enabled=False` 时**所有**字段都不参与计算，使零成本路径与升级前逐位
    一致（spec Scenario "Request without new optional fields is unchanged"）。
    """

    enabled: bool = False
    commission_rate: Decimal = DEFAULT_COMMISSION_RATE
    stamp_tax_rate: Decimal = DEFAULT_STAMP_TAX_RATE
    slippage_bps: Decimal = DEFAULT_SLIPPAGE_BPS

    def to_dict(self) -> dict:
        """快照形态：字符串化的 Decimal（JSON 没有 Decimal，浮点会丢精度）。"""
        return {
            "enabled": self.enabled,
            "commission_rate": str(self.commission_rate),
            "stamp_tax_rate": str(self.stamp_tax_rate),
            "slippage_bps": str(self.slippage_bps),
        }

    @classmethod
    def from_mapping(cls, raw: Optional[Mapping]) -> "CostModel":
        """从请求体构造。缺字段走默认；非法值明确报错（不静默钳制）。"""
        if not raw:
            return cls()
        if not isinstance(raw, Mapping):
            raise ValueError("cost_model must be an object")
        unknown = set(raw) - {"enabled", "commission_rate", "stamp_tax_rate", "slippage_bps"}
        if unknown:
            raise ValueError(f"cost_model has unknown field(s): {sorted(unknown)}")

        def _dec(key: str, default: Decimal) -> Decimal:
            if key not in raw or raw[key] is None:
                return default
            try:
                v = Decimal(str(raw[key]))
            except Exception:
                raise ValueError(f"cost_model.{key} must be a number, got {raw[key]!r}") from None
            if v < 0:
                raise ValueError(f"cost_model.{key} must be non-negative, got {raw[key]!r}")
            return v

        enabled = raw.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ValueError(f"cost_model.enabled must be a boolean, got {enabled!r}")
        return cls(
            enabled=enabled,
            commission_rate=_dec("commission_rate", DEFAULT_COMMISSION_RATE),
            stamp_tax_rate=_dec("stamp_tax_rate", DEFAULT_STAMP_TAX_RATE),
            slippage_bps=_dec("slippage_bps", DEFAULT_SLIPPAGE_BPS),
        )


@dataclass
class CostTotals:
    """逐笔累加的费用汇总（进运行记录，可对账）。"""

    commission: Decimal = Decimal(0)
    stamp_tax: Decimal = Decimal(0)
    slippage: Decimal = Decimal(0)
    fills: int = 0

    def to_dict(self) -> dict:
        return {
            "commission": str(self.commission),
            "stamp_tax": str(self.stamp_tax),
            "slippage": str(self.slippage),
            "total": str(self.commission + self.stamp_tax),
            "fills": self.fills,
        }


@dataclass(frozen=True)
class FillCost:
    """一笔成交的成本分解。

    `effective_price` 是**实际成交价**（已调滑点），`slippage_cost` 是滑点造成的
    额外金额（只用于汇总展示——现金扣减已经体现在 effective_price 里，不能
    再加一次，否则滑点被计两遍）。
    """

    effective_price: Decimal
    notional: Decimal
    commission: Decimal
    stamp_tax: Decimal
    slippage_cost: Decimal
    cash_delta: Decimal


def apply_costs(
    *,
    side: str,
    quantity: Decimal,
    price: Decimal,
    model: CostModel,
) -> FillCost:
    """给定方向、数量、信号价，算出成交价与各项费用。

    买入：`effective = price * (1 + slippage)`，现金 `-= notional + commission`。
    卖出：`effective = price * (1 - slippage)`，现金 `+= notional - commission - tax`。

    `side` 只认 `"BUY"`/`"SELL"`（与 `ctx.buy/sell` 及 `Signal.side` 一致）。
    """
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side must be BUY or SELL, got {side!r}")
    qty = abs(Decimal(quantity))
    px = Decimal(price)

    if not model.enabled:
        notional = qty * px
        return FillCost(
            effective_price=px, notional=notional, commission=Decimal(0),
            stamp_tax=Decimal(0), slippage_cost=Decimal(0),
            cash_delta=-notional if side == "BUY" else notional,
        )

    slip = model.slippage_bps / BPS
    if side == "BUY":
        effective = px * (Decimal(1) + slip)
        notional = qty * effective
        commission = notional * model.commission_rate
        stamp = Decimal(0)
        cash_delta = -(notional + commission)
    else:
        effective = px * (Decimal(1) - slip)
        notional = qty * effective
        commission = notional * model.commission_rate
        stamp = notional * model.stamp_tax_rate
        cash_delta = notional - commission - stamp

    return FillCost(
        effective_price=effective,
        notional=notional,
        commission=commission,
        stamp_tax=stamp,
        slippage_cost=qty * abs(effective - px),
        cash_delta=cash_delta,
    )


def accumulate(totals: CostTotals, fill: FillCost) -> None:
    totals.commission += fill.commission
    totals.stamp_tax += fill.stamp_tax
    totals.slippage += fill.slippage_cost
    totals.fills += 1
