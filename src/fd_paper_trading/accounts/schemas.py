"""Pydantic schemas for paper accounts."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class AccountCreate(BaseModel):
    name: str = Field(default="default", min_length=1, max_length=120)
    starting_balance: Decimal | None = Field(default=None, ge=0)


class AccountDeposit(BaseModel):
    # Signed: positive deposits, negative withdrawals.
    amount: Decimal


class AccountReset(BaseModel):
    starting_balance: Decimal | None = Field(default=None, ge=0)


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    venue: str
    currency: str
    starting_balance: Decimal
    cash_balance: Decimal
    created_at: datetime


class AccountSummary(BaseModel):
    id: uuid.UUID
    name: str
    venue: str
    currency: str
    cash_balance: Decimal
    # cash + unrealized PnL. Until the market-data layer marks positions to
    # market, equity equals cash balance.
    equity: Decimal
    open_positions: int
    open_orders: int
    created_at: datetime
