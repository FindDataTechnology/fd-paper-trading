"""Request/response schemas for the trading API (orders, fills, positions)."""

import uuid
from datetime import datetime
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
