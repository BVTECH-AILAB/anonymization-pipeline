"""Application factory and ASGI entry point (``anonymization_pipeline.main:app``)."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from anonymization_pipeline import __version__
from anonymization_pipeline.api import health
from anonymization_pipeline.config import Settings, get_settings
from anonymization_pipeline.detectors import load_resources
from anonymization_pipeline.errors import register_exception_handlers
from anonymization_pipeline.logging_setup import setup_logging
from anonymization_pipeline.middleware import RequestIdMiddleware
from anonymization_pipeline.pipeline_config import load_pipeline_config

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create shared resources on startup and release them on shutdown.

    Clients (httpx clients ...) live on ``app.state``, never in module-level
    globals. The pipeline configuration and the detector files are loaded
    here: an invalid file stops the startup instead of failing every request.
    """
    settings: Settings = app.state.settings
    app.state.pipeline_config = load_pipeline_config(settings)
    load_resources()
    logger.info(
        "Service started: name=%s version=%s environment=%s",
        settings.app_name,
        __version__,
        settings.environment,
    )
    try:
        yield
    finally:
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
    return app


def build_app() -> FastAPI:
    """Production entry point: logging first, then the app."""
    settings = get_settings()
    setup_logging(settings)
    return create_app(settings)


app = build_app()
