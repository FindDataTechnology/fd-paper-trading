"""Async database engine, session factory, and schema bootstrap."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


@dataclass
class Database:
    """Container for an async engine and its session factory."""

    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]


def create_database(database_url: str) -> Database:
    """Create a ``Database`` bound to ``database_url``.

    SQLite (used for tests/dev) needs ``check_same_thread=False`` and a
    ``StaticPool`` so an in-memory database survives across sessions.
    """
    is_sqlite = database_url.startswith("sqlite")
    connect_args: dict = {}
    kwargs: dict = {}
    if is_sqlite:
        connect_args["check_same_thread"] = False
        kwargs["poolclass"] = StaticPool

    engine = create_async_engine(
        database_url,
        echo=False,
        connect_args=connect_args,
        **kwargs,
    )
    return Database(
        engine=engine,
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
    )


async def init_db(database: Database) -> None:
    """Create all tables. Used for SQLite/tests and first-run bootstrapping."""
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
