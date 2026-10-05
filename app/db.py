"""Async SQLAlchemy engine/session bound to the shared PostgreSQL database."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from .config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    settings = get_settings()
    url, connect_args = settings.async_database_url()
    kwargs: dict = {"connect_args": connect_args, "future": True}
    # Under the test runner each test gets its own event loop; a persistent
    # asyncpg pool would bind connections to a dead loop, so disable pooling.
    if os.environ.get("DB_DISABLE_POOL"):
        kwargs["poolclass"] = NullPool
    else:
        kwargs["pool_pre_ping"] = True
    return create_async_engine(url, **kwargs)


_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine, _sessionmaker
    if _engine is None:
        _engine = _make_engine()
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: a session that commits on success, rolls back on error."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def now_utc() -> datetime:
    """Naive UTC datetime — Prisma columns are `timestamp without time zone`."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
