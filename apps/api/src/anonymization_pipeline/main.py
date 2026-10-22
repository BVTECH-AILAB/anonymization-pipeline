"""Application factory and ASGI entry point (``anonymization_pipeline.main:app``)."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from anonymization_core.detect.ner import AilabNER
from anonymization_core.factory import (
    build_pipeline,
    load_models,
)
from anonymization_core.pipeline import Pipeline
from fastapi import FastAPI

from anonymization_pipeline import __version__
from anonymization_pipeline.api import anonymization, health
from anonymization_pipeline.config import Settings, get_settings
from anonymization_pipeline.errors import register_exception_handlers
from anonymization_pipeline.logging_setup import setup_logging
from anonymization_pipeline.middleware import RequestIdMiddleware
from anonymization_pipeline.services.jobs import JobManager

logger = logging.getLogger(__name__)

PURGE_INTERVAL_SECONDS = 60
MODEL_RETRY_SECONDS = 30


async def _load_loop(pipeline: Pipeline) -> None:
    """Load the models, retrying until every engine is ready.

    Until then ``/readyz`` stays 503 (fail-closed).
    """
    while not await asyncio.to_thread(load_models, pipeline):
        await asyncio.sleep(MODEL_RETRY_SECONDS)


async def _purge_loop(jobs: JobManager) -> None:
    """Delete expired documents periodically; an error never stops the loop."""
    while True:
        await asyncio.sleep(PURGE_INTERVAL_SECONDS)
        try:
            jobs.purge_expired()
        except Exception as exc:
            logger.error("Purge of expired documents failed: %s", type(exc).__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create shared resources on startup and release them on shutdown.

    The pipeline and the job manager live on ``app.state``, never in
    module-level globals. Models load in the background: the API answers
    the probes at once and ``/readyz`` turns ready when the weights are loaded.
    """
    settings: Settings = app.state.settings
    pipeline = build_pipeline(settings.pipeline_config())
    jobs = JobManager(
        pipeline, settings, Path(settings.work_dir) if settings.work_dir else None
    )
    app.state.pipeline = pipeline
    app.state.jobs = jobs
    tasks = [asyncio.create_task(_purge_loop(jobs))]
    if settings.preload_models:
        tasks.append(asyncio.create_task(_load_loop(pipeline)))
    logger.info(
        "Service started: name=%s version=%s environment=%s",
        settings.app_name,
        __version__,
        settings.environment,
    )
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        jobs.shutdown()
        if isinstance(pipeline.ner, AilabNER):
            pipeline.ner.close()
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
    app.include_router(anonymization.router)
    return app


def build_app() -> FastAPI:
    """Production entry point: logging first, then the app."""
    settings = get_settings()
    setup_logging(settings)
    return create_app(settings)


app = build_app()
