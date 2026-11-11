"""SQLAlchemy ORM models.

Money/quantities are ``Numeric`` to avoid float drift. Status values are stored
as plain strings (validated in the Pydantic schemas) for portability across
Postgres and SQLite.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB as _PG_JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

# Postgres stores JSONB; SQLite (tests/dev) degrades to generic JSON.
JSONB = _PG_JSONB().with_variant(JSON(), "sqlite")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---- Status enums (string-backed for cross-DB portability) ----

class OrderSide:
    BUY = "BUY"
    SELL = "SELL"


class OrderType:
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


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


class StrategyRunMode:
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"


class StrategyRunStatus:
    # Backtest lifecycle: QUEUED -> RUNNING -> COMPLETED | FAILED
    QUEUED = "QUEUED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    # Paper lifecycle: STARTING -> RUNNING -> STOPPED | ERROR
    STARTING = "STARTING"
    STOPPED = "STOPPED"
    ERROR = "ERROR"
    # Shared by both lifecycles.
    RUNNING = "RUNNING"


# Runs holding resources (a background task / supervised runner).
ACTIVE_RUN_STATUSES = (
    StrategyRunStatus.QUEUED,
    StrategyRunStatus.STARTING,
    StrategyRunStatus.RUNNING,
)


# Fixed id of the seeded ``default`` group (created by the quant-strategies
# migration). Keep in sync with alembic/versions/7f3a2b91c4d0_*.py.
DEFAULT_GROUP_ID = uuid.UUID("8f3a6b21-0000-4000-8000-000000000001")

# Fallback limits when a user has no group or a key is missing from config.
DEFAULT_GROUP_CONFIG = {
    "max_active_strategies": 3,
    "max_backtest_days": 730,
    "max_orders_per_day": 20,
    "allow_custom_code": False,
    "allowed_symbols": None,
    # Weight-track runs trade a universe. 20 names keeps one full rotation inside
    # the default daily order cap, so the cap constrains per-day churn rather than
    # making a legal rebalance impossible on its own.
    "max_universe_size": 20,
}


def group_config(user: "User | None", key: str):
    """Resolve one group-config key for a user, falling back to defaults."""
    config = user.group.config if user is not None and user.group is not None else {}
    return config.get(key, DEFAULT_GROUP_CONFIG[key])


class UserGroup(Base):
    __tablename__ = "user_groups"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    # Per-group limits; see DEFAULT_GROUP_CONFIG for the recognized keys.
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_groups.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    group: Mapped["UserGroup | None"] = relationship(lazy="selectin")
    accounts: Mapped[list["Account"]] = relationship(
        back_populates="user", lazy="selectin", cascade="all, delete-orphan"
    )
    tokens: Mapped[list["ApiToken"]] = relationship(
        back_populates="user", lazy="selectin", cascade="all, delete-orphan"
    )
    strategies: Mapped[list["Strategy"]] = relationship(
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
    # Idempotency: one order per (account, client_order_id). NULL client ids
    # stay multi-allowed (NULLs are distinct in a unique index on both PG and
    # SQLite), so manually-placed orders are unaffected.
    __table_args__ = (
        UniqueConstraint(
            "account_id", "client_order_id", name="uq_orders_account_client_order_id"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Set when the order was placed by a strategy run (live paper or backtest
    # replay); NULL for manually-placed orders.
    strategy_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategy_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    client_order_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    instrument_id: Mapped[str] = mapped_column(String(80), index=True)  # e.g. "AAPL.NASDAQ"
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    side: Mapped[str] = mapped_column(String(8))                       # BUY / SELL
    order_type: Mapped[str] = mapped_column(String(8))                 # MARKET / LIMIT / STOP / STOP_LIMIT
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    stop_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=OrderStatus.PENDING, index=True)
    filled_quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=Decimal("0"))
    avg_fill_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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


class Strategy(Base):
    """A user's strategy definition.

    ``spec`` is the validated strategy spec (template track or code track);
    editing the spec bumps ``version`` so historical runs pin what they ran.
    """

    __tablename__ = "strategies"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    spec: Mapped[dict] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    user: Mapped["User"] = relationship(back_populates="strategies")
    runs: Mapped[list["StrategyRun"]] = relationship(
        back_populates="strategy", lazy="selectin", cascade="all, delete-orphan"
    )


class StrategyRun(Base):
    """One execution of a strategy: a backtest or a live paper run.

    Pins ``strategy_version`` at start so later spec edits don't rewrite
    history. ``params`` holds run-time inputs (backtest window, initial cash,
    paper account); ``stats`` the computed headline stats on completion.
    """

    __tablename__ = "strategy_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), index=True
    )
    strategy_version: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(10))  # BACKTEST / PAPER
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default=StrategyRunStatus.QUEUED, index=True)
    params: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    stats: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Weight-track runs only. ``universe`` is copied from the spec at run start so
    #: editing the strategy afterwards cannot rewrite what a recorded run traded
    #: (the same reason ``strategy_version`` is pinned). ``weight_snapshots`` holds
    #: one entry per rebalance (target, actual, instructions with scaling flags);
    #: ``data_gaps`` holds the (date, symbol) pairs valued from a stale close.
    #: Null on single-symbol runs, which have no universe and no rebalance.
    universe: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    weight_snapshots: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    data_gaps: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    #: Backtest v2 records (design D4/D5/D6). All nullable with no default: a run
    #: that predates these capabilities keeps NULL, which is the honest shape of
    #: "this run had no cost model / no benchmark / no windows" — and is what makes
    #: the backward-compatibility regression meaningful instead of vacuous.
    #: ``cost_config`` is snapshotted per run because "the same strategy scored
    #: differently" is first explained by whether the cost parameters changed.
    cost_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    cost_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    benchmark_config: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    benchmark_results: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    walkforward_windows: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    param_grid: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    scan_results: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    #: Machine-readable in-sample marker (design D7): the scan's best row is the
    #: output most easily misread as a recommendation, so the disclaimer is data.
    in_sample: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Single-writer lease. During a rolling update two pods overlap, and both
    #: would poll the same bars and place the same orders — the "already
    #: processed" guard is written *after* the orders go in, so it cannot stop
    #: the duplicate. The owner is a per-process token; the deadline lets a
    #: crashed owner expire instead of blocking the run forever.
    runner_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    runner_lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    stopped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    strategy: Mapped["Strategy"] = relationship(back_populates="runs")
    equity_points: Mapped[list["StrategyEquityPoint"]] = relationship(
        back_populates="run", lazy="selectin", cascade="all, delete-orphan"
    )
    events: Mapped[list["StrategyEvent"]] = relationship(
        back_populates="run", lazy="selectin", cascade="all, delete-orphan",
        order_by="StrategyEvent.ts",
    )


class StrategyEquityPoint(Base):
    """One equity sample of a run: per bar (backtest) or per day (paper)."""

    __tablename__ = "strategy_equity_points"
    __table_args__ = (
        UniqueConstraint("run_id", "date", name="uq_strategy_equity_points_run_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("strategy_runs.id", ondelete="CASCADE"), index=True
    )
    date: Mapped[date] = mapped_column(Date)
    equity: Mapped[Decimal] = mapped_column(Numeric(20, 4))

    run: Mapped["StrategyRun"] = relationship(back_populates="equity_points")


class StrategyEvent(Base):
    """One observable event during a strategy run (design D8/task 7.4).

    Persisted so the web UI can show a live-run timeline and history; also
    broadcast over ``/ws`` to the owning user. ``type`` is one of
    ``StrategyEventType``; ``data`` carries type-specific detail (order ids,
    rejection reasons, etc.).
    """

    __tablename__ = "strategy_events"
    __table_args__ = (
        Index("ix_strategy_events_run_ts", "run_id", "ts"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("strategy_runs.id", ondelete="CASCADE"), index=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    type: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    run: Mapped["StrategyRun"] = relationship(back_populates="events")


class StrategyEventType:
    """Strategy run event types (broadcast + persisted)."""

    STATE_CHANGE = "state_change"  # run status transition
    ORDER = "order"                # an order was placed (filled/rejected/...)
    REJECTION = "rejection"        # a signal was rejected (daily cap, etc.)
    ERROR = "error"                # a tick failed (logged, run continues)
    # Weight-track runs (strategy-weight-mode). A REBALANCE marks a decision
    # point of the run's cadence (with the counts it produced); a SKIP marks an
    # instruction that was never sent to the service at all — a halted symbol,
    # which is absent from the order stream rather than refused by it.
    REBALANCE = "rebalance"        # a weight run reached a rebalance point
    SKIP = "skip"                  # an instruction was skipped before placing


class OrderIntentRecord(Base):
    """纸面下单的「意图票」（spec `order-intent-export`）。

    为什么单独一张表而不是往 orders 上加列：票记的是**决定**（依据哪一期权重、
    当时风控闸怎么判的），单记的是**动作**（成交了什么）。两者生命周期不同——
    一张票可能没有对应的单（被拒/被当日上限截住），也可能是同一次调仓里多张票
    指向同一条策略运行。混在一张表里，任何"按单统计"的查询都会被票的行数污染。

    `intent` 列存契约实例（`OrderIntent.to_dict()` 的逐字快照）——与账本同一手法：
    契约变了也能读回当时的形状。`exportable` / `blocked_reason` 是**记录侧的判断**，
    不是契约字段（契约不认未知字段，硬塞会让校验绿不了）。
    """

    __tablename__ = "order_intents"

    intent_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    #: 票来自哪一次策略运行与哪一期权重（`derived_from` 的落地形态）。
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("strategy_runs.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    as_of: Mapped[date] = mapped_column(Date, index=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    side: Mapped[str] = mapped_column(String(8))                  # buy / sell
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    order_type: Mapped[str] = mapped_column(String(8), default="market")
    #: 契约实例的逐字快照（含 derived_from / risk_verdict / paper_only）。
    intent: Mapped[dict] = mapped_column(JSONB)
    #: 按契约这笔票导不导得出去。`risk_verdict.status != "ok"` → False。
    #: **票照记**：不记就看不见"这笔单本不该下"，那才是真正要暴露的事实。
    exportable: Mapped[bool] = mapped_column(Boolean, default=False)
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 对应成交动作：下单成功指向 `orders.id`；被拒/被上限截住时为空并留原因。
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True
    )
    outcome: Mapped[str] = mapped_column(String(20))   # placed / rejected / capped / skipped
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
