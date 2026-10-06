"""Async engine, session factory and the FastAPI session dependency."""

import logging
from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from agenas_service.config import Settings
from agenas_service.errors import ServiceUnavailableError

logger = logging.getLogger(__name__)


def build_engine(settings: Settings) -> AsyncEngine | None:
    """Create the engine. No connection is opened until first use.

    A database that is down at startup, or not configured at all, does not
    crash the pod: ``/readyz`` reports it and Kubernetes keeps the pod out
    of the Service.

    Returns:
        The engine, or None when ``database_url`` is not set.
    """
    if not settings.database_url:
        logger.warning("Database URL not configured: database features are unavailable")
        return None
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        connect_args={"timeout": settings.db_connect_timeout},
    )


def build_sessionmaker(
    engine: AsyncEngine | None,
) -> async_sessionmaker[AsyncSession] | None:
    """Create the session factory stored on ``app.state``."""
    if engine is None:
        return None
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request.

    Raises:
        ServiceUnavailableError: If the database is not configured.
    """
    sessionmaker: async_sessionmaker[AsyncSession] | None = (
        request.app.state.sessionmaker
    )
    if sessionmaker is None:
        raise ServiceUnavailableError()
    async with sessionmaker() as session:
        yield session


async def ping(engine: AsyncEngine | None) -> bool:
    """Return True if the database answers ``SELECT 1``.

    Only the exception type is logged: driver messages can contain the
    host, the user or the database name.
    """
    if engine is None:
        return False
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("Database readiness check failed: %s", type(exc).__name__)
        return False
    return True
