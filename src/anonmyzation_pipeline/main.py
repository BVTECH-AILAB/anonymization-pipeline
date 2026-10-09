"""Application factory and ASGI entry point (``anonmyzation_pipeline.main:app``)."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from anonmyzation_pipeline import __version__
from anonmyzation_pipeline.api import health, items
from anonmyzation_pipeline.config import Settings, get_settings
from anonmyzation_pipeline.db.engine import build_engine, build_sessionmaker
from anonmyzation_pipeline.errors import register_exception_handlers
from anonmyzation_pipeline.logging_setup import setup_logging
from anonmyzation_pipeline.middleware import RequestIdMiddleware

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create shared resources on startup and release them on shutdown.

    Clients (DB engine, httpx clients ...) live on ``app.state``, never in
    module-level globals.
    """
    settings: Settings = app.state.settings
    engine = build_engine(settings)
    app.state.engine = engine
    app.state.sessionmaker = build_sessionmaker(engine)
    logger.info(
        "Service started: name=%s version=%s environment=%s",
        settings.app_name,
        __version__,
        settings.environment,
    )
    try:
        yield
    finally:
        if engine is not None:
            await engine.dispose()
        logger.info("Service stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application.

    Logging is configured by the caller (``build_app`` in production, the
    ``log_stream`` fixture in tests), not here.

    Args:
        settings: Explicit settings (tests). Defaults to ``get_settings()``.
    """
    settings = settings or get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )
    app.state.settings = settings
    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(items.router)
    return app


def build_app() -> FastAPI:
    """Production entry point: logging first, then the app."""
    settings = get_settings()
    setup_logging(settings)
    return create_app(settings)


app = build_app()
