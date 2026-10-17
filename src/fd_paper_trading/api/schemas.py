"""Request/response schemas for the trading API (orders, fills, positions)."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    quantity: Decimal
    price: Decimal
    side: str
    ts: datetime


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    client_order_id: str | None = None
    strategy_run_id: uuid.UUID | None = None
    instrument_id: str
    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    limit_price: Decimal | None = None
    status: str
    filled_quantity: Decimal
    avg_fill_price: Decimal | None = None
    reject_reason: str | None = None
    created_at: datetime
    updated_at: datetime
    fills: list[FillOut] = []


class PositionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    instrument_id: str
    symbol: str
    quantity: Decimal
    avg_price: Decimal
    realized_pnl: Decimal


class InstrumentSearchResultOut(BaseModel):
    """A symbol-search hit."""

    symbol: str
    name: str
    venue: str


class BarOut(BaseModel):
    """A single OHLCV bar (unix-seconds timestamp)."""

    ts: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


class OrderCreate(BaseModel):
    account_id: uuid.UUID
    symbol: str = Field(min_length=1, max_length=40)
    side: str
    order_type: str
    quantity: Decimal = Field(gt=0)
    limit_price: Decimal | None = Field(default=None, gt=0)
    client_order_id: str | None = Field(default=None, max_length=80)

    @field_validator("side")
    @classmethod
    def _valid_side(cls, v: str) -> str:
        if v not in ("BUY", "SELL"):
            raise ValueError("side must be BUY or SELL")
        return v

    @field_validator("order_type")
    @classmethod
    def _valid_type(cls, v: str) -> str:
        if v not in ("MARKET", "LIMIT"):
            raise ValueError("order_type must be MARKET or LIMIT")
        return v

    @model_validator(mode="after")
    def _limit_requires_price(self) -> "OrderCreate":
        if self.order_type == "LIMIT" and self.limit_price is None:
            raise ValueError("limit_price is required for LIMIT orders")
        return self


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


class StrategyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    spec: dict


class StrategyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    spec: dict | None = None


class StrategyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None = None
    spec: dict
    spec_summary: str | None = None
    version: int
    created_at: datetime
    updated_at: datetime


class StrategyTemplateOut(BaseModel):
    """One entry of the built-in template catalog."""

    id: str
    name: str
    description: str
    params_schema: dict


class StrategyCatalogOut(BaseModel):
    """The authoring surface for every spec kind (task 2.2).

    ``weight_track`` is deliberately an untyped dict: it describes a surface that
    will grow (more source forms, more limits), and pinning its shape here would
    make every addition a breaking schema change for clients that only want to
    read it.
    """

    templates: list[StrategyTemplateOut]
    spec_kinds: list[str]
    weight_track: dict


class CostModelRequest(BaseModel):
    """Cost model for one backtest (design D4). Disabled by default so a request
    that omits it behaves exactly as before this field existed."""

    enabled: bool = False
    #: A-share defaults, not the US magnitudes FinRL-X uses: 0.025% commission
    #: (both sides), 0.05% stamp tax (sell only), 5bps slippage (both sides).
    commission_rate: Decimal = Field(default=Decimal("0.00025"), ge=0)
    stamp_tax_rate: Decimal = Field(default=Decimal("0.0005"), ge=0)
    slippage_bps: Decimal = Field(default=Decimal("5"), ge=0)


class BacktestRequest(BaseModel):
    start: date
    end: date
    initial_cash: Decimal = Field(default=Decimal("100000"), gt=0)
    #: All v2 fields are optional with backward-compatible defaults (spec
    #: "Request without new optional fields is unchanged").
    cost_model: CostModelRequest | None = None
    benchmarks: list[str] | None = None
    train_window: int | None = Field(default=None, gt=0)
    test_window: int | None = Field(default=None, gt=0)
    step: int | None = Field(default=None, gt=0)
    param_grid: dict[str, list] | None = None
    sort_by: str = "sharpe"

    @model_validator(mode="after")
    def _window_ok(self) -> "BacktestRequest":
        if self.start > self.end:
            raise ValueError("start must be on or before end")
        # Walk-forward needs all three, or none: two of three is a request that
        # silently means something the caller did not ask for.
        wf = (self.train_window, self.test_window, self.step)
        if any(v is not None for v in wf) and not all(v is not None for v in wf):
            raise ValueError(
                "walk-forward needs train_window, test_window and step together "
                f"(got train_window={self.train_window}, test_window={self.test_window}, "
                f"step={self.step})"
            )
        return self

    @property
    def walk_forward_enabled(self) -> bool:
        return self.train_window is not None


class StrategyRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    strategy_id: uuid.UUID
    strategy_version: int
    mode: str
    account_id: uuid.UUID | None = None
    status: str
    params: dict | None = None
    stats: dict | None = None
    error: str | None = None
    # Weight-track runs only, null on single-symbol runs: the universe pinned at
    # run start, the per-rebalance snapshots (target/actual weights and
    # instruction details with scaling flags), and the (date, symbol) pairs
    # valued from a stale close.
    universe: list[str] | None = None
    weight_snapshots: list | None = None
    data_gaps: list | None = None
    # Backtest v2 records. All null on runs that predate the capabilities, which
    # is what lets the web panel distinguish "not enabled" from "enabled and
    # produced nothing".
    cost_config: dict | None = None
    cost_summary: dict | None = None
    benchmark_config: list[str] | None = None
    benchmark_results: list | None = None
    walkforward_windows: list | None = None
    param_grid: dict | None = None
    scan_results: list | None = None
    in_sample: bool | None = None
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    created_at: datetime


class EquityPointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date: date
    equity: Decimal


class StrategyEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    ts: datetime
    type: str
    message: str
    data: dict | None = None


class StrategyRunDetailOut(StrategyRunOut):
    equity_points: list[EquityPointOut] = []
    events: list[StrategyEventOut] = []


class PaperRunRequest(BaseModel):
    """Start a live paper run on a paper account."""
    account_id: uuid.UUID


# ---------------------------------------------------------------------------
# User groups (admin-managed; design D7)
# ---------------------------------------------------------------------------


class UserGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    config: dict = Field(default_factory=dict)


class UserGroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    config: dict | None = None


class UserGroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    config: dict
    created_at: datetime


class AssignUserGroup(BaseModel):
    group_id: uuid.UUID
