"""SQLAlchemy ORM models.

Money/quantities are ``Numeric`` to avoid float drift. Status values are stored
as plain strings (validated in the Pydantic schemas) for portability across
Postgres and SQLite.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---- Status enums (string-backed for cross-DB portability) ----

class OrderSide:
    BUY = "BUY"
    SELL = "SELL"


class OrderType:
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus:
    PENDING = "PENDING"                # accepted by API, not yet on the engine
    SUBMITTED = "SUBMITTED"            # handed to the execution engine
    OPEN = "OPEN"                      # resting on the engine
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


# Active (non-terminal) statuses, useful for queries.
ACTIVE_ORDER_STATUSES = (
    OrderStatus.PENDING,
    OrderStatus.SUBMITTED,
    OrderStatus.OPEN,
    OrderStatus.PARTIALLY_FILLED,
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    accounts: Mapped[list["Account"]] = relationship(
        back_populates="user", lazy="selectin", cascade="all, delete-orphan"
    )
    tokens: Mapped[list["ApiToken"]] = relationship(
        back_populates="user", lazy="selectin", cascade="all, delete-orphan"
    )


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), default="default")
    # "sha256$<hex>" - API tokens are long random strings, not passwords.
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="tokens")


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120), default="default")
    # Per-account simulated venue in the execution engine, e.g. "PAPER-A1B2C3D4E5F6".
    venue: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    starting_balance: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal("100000"))
    cash_balance: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal("100000"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user: Mapped["User"] = relationship(back_populates="accounts")
    orders: Mapped[list["Order"]] = relationship(
        back_populates="account", lazy="selectin", cascade="all, delete-orphan"
    )
    positions: Mapped[list["Position"]] = relationship(
        back_populates="account", lazy="selectin", cascade="all, delete-orphan"
    )


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    client_order_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    instrument_id: Mapped[str] = mapped_column(String(80), index=True)  # e.g. "AAPL.NASDAQ"
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    side: Mapped[str] = mapped_column(String(8))                       # BUY / SELL
    order_type: Mapped[str] = mapped_column(String(8))                 # MARKET / LIMIT
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=OrderStatus.PENDING, index=True)
    filled_quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=Decimal("0"))
    avg_fill_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    account: Mapped["Account"] = relationship(back_populates="orders")
    fills: Mapped[list["Fill"]] = relationship(
        back_populates="order", lazy="selectin", cascade="all, delete-orphan"
    )


class Fill(Base):
    __tablename__ = "fills"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    instrument_id: Mapped[str] = mapped_column(String(80))
    side: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    price: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    order: Mapped["Order"] = relationship(back_populates="fills")


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (
        Index("ix_positions_account_instrument", "account_id", "instrument_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    instrument_id: Mapped[str] = mapped_column(String(80))
    symbol: Mapped[str] = mapped_column(String(40))
    # Signed: positive = long, negative = short.
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=Decimal("0"))
    avg_price: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=Decimal("0"))
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal("0"))

    account: Mapped["Account"] = relationship(back_populates="positions")


class BalanceTransaction(Base):
    __tablename__ = "balance_transactions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(20))  # deposit / withdrawal / trade / reset
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4))  # signed
    balance_after: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
