"""Alembic environment (async, Postgres only).

The container entrypoint runs ``alembic upgrade head`` on every pod start.
With ``replicas: 2`` two pods would migrate at the same time, so the upgrade
runs under a Postgres advisory lock: the second pod waits, then finds the
schema already at head and does nothing.
"""

import asyncio
import sys

from alembic import context
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import create_async_engine

from anonmyzation_pipeline.config import get_settings
from anonmyzation_pipeline.db.models import Base
from anonmyzation_pipeline.logging_setup import setup_logging

# Arbitrary constant, unique per service database.
MIGRATION_LOCK_ID = 727_100_001

settings = get_settings()
# Offline mode prints SQL on stdout: keep log lines out of it.
setup_logging(settings, stream=sys.stderr if context.is_offline_mode() else None)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Render SQL without a connection (``alembic upgrade head --sql``)."""
    context.configure(
        # Only the dialect matters here: no credentials needed.
        url=settings.database_url or "postgresql+asyncpg://",
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_locked(connection: Connection) -> None:
    connection.execute(text("SELECT pg_advisory_lock(:id)"), {"id": MIGRATION_LOCK_ID})
    connection.commit()
    try:
        context.configure(
            connection=connection, target_metadata=target_metadata, compare_type=True
        )
        with context.begin_transaction():
            context.run_migrations()
    finally:
        connection.execute(
            text("SELECT pg_advisory_unlock(:id)"), {"id": MIGRATION_LOCK_ID}
        )
        connection.commit()


async def run_migrations_online() -> None:
    if not settings.database_url:
        raise SystemExit("ANONPIP_DATABASE_URL is not set: cannot run migrations")
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_locked)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
