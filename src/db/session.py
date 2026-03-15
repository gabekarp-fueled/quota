"""Async SQLAlchemy engine and session factory.

Call init_db(database_url) once during app startup.
Use get_db() as a FastAPI dependency for DB sessions.
Use get_db_optional() when DB may not be configured.
"""

import logging
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logger = logging.getLogger(__name__)

_engine = None
_session_factory: async_sessionmaker | None = None


def init_db(database_url: str):
    """Initialize the async engine and session factory. Call once at startup."""
    global _engine, _session_factory

    # Railway uses postgres://, SQLAlchemy needs postgresql+asyncpg://
    url = database_url.replace("postgres://", "postgresql+asyncpg://")
    if not url.startswith("postgresql+asyncpg://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://")

    _engine = create_async_engine(url, echo=False, pool_size=5, max_overflow=10)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    logger.info("DB engine initialized")
    return _engine, _session_factory


def get_session_factory() -> async_sessionmaker | None:
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a session, raises if DB not configured."""
    if not _session_factory:
        raise RuntimeError("Database not configured (DATABASE_URL not set)")
    async with _session_factory() as session:
        yield session


async def get_db_optional() -> AsyncGenerator[AsyncSession | None, None]:
    """FastAPI dependency — yields None if DB not configured, session otherwise."""
    if not _session_factory:
        yield None
        return
    async with _session_factory() as session:
        yield session
