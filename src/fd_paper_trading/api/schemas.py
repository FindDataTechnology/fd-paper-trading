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


class BacktestRequest(BaseModel):
    start: date
    end: date
    initial_cash: Decimal = Field(default=Decimal("100000"), gt=0)

    @model_validator(mode="after")
    def _window_ok(self) -> "BacktestRequest":
        if self.start > self.end:
            raise ValueError("start must be on or before end")
        return self


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
