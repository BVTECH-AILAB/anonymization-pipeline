"""Application factory and ASGI entry point (``anonymization_pipeline.main:app``)."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from anonymization_pipeline import __version__, ocr
from anonymization_pipeline.api import anonymization, catalog, health
from anonymization_pipeline.config import Settings, get_settings
from anonymization_pipeline.detectors import DetectorUnavailable, load_resources
from anonymization_pipeline.detectors.ner import load_engine
from anonymization_pipeline.errors import register_exception_handlers
from anonymization_pipeline.logging_setup import setup_logging
from anonymization_pipeline.middleware import RequestIdMiddleware, UploadLimitMiddleware
from anonymization_pipeline.pipeline_config import PipelineConfig, load_pipeline_config
from anonymization_pipeline.services.anonymization import (
    Anonymized,
    Progress,
    anonymize,
)
from anonymization_pipeline.services.jobs import JobRegistry, Runner

logger = logging.getLogger(__name__)

PURGE_EVERY_S = 60.0
# multipart framing and form fields on top of the file
UPLOAD_OVERHEAD = 256 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create shared resources on startup and release them on shutdown.

    Clients (httpx clients ...) live on ``app.state``, never in module-level
    globals. The pipeline configuration and the detector files are loaded
    here: an invalid file stops the startup instead of failing every request.
    The models load in the background: /readyz answers 503 until they are ready.
    """
    settings: Settings = app.state.settings
    cfg = load_pipeline_config(settings)
    app.state.pipeline_config = cfg
    load_resources()
    app.state.ner = None
    app.state.ocr = None
    app.state.models_ready = False
    models = asyncio.create_task(_load_models(app, cfg), name="models")
    jobs: JobRegistry | None = getattr(app.state, "jobs", None)
    if jobs is None:
        jobs = JobRegistry(settings, run=_runner(app))
        app.state.jobs = jobs
    await jobs.start()
    purge = asyncio.create_task(_purge_loop(jobs), name="jobs-purge")
    logger.info(
        "Service started: name=%s version=%s environment=%s",
        settings.app_name,
        __version__,
        settings.environment,
    )
    try:
        yield
    finally:
        for task in (purge, models):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await jobs.stop()
        logger.info("Service stopped")


async def _load_models(app: FastAPI, cfg: PipelineConfig) -> None:
    """Load the configured models off the event loop; the service is ready only after."""
    try:
        if cfg.detection.ner.enabled:
            app.state.ner = await asyncio.to_thread(load_engine, cfg.detection.ner)
        if cfg.ocr.enabled:
            app.state.ocr = await asyncio.to_thread(ocr.load_engine, cfg.ocr)
    except DetectorUnavailable as exc:
        logger.error("Model unavailable, service not ready: %s", exc)
        return
    app.state.models_ready = True


def _runner(app: FastAPI) -> Runner:
    """The anonymization of a job, with the models loaded at startup."""

    def run(data: bytes, cfg: PipelineConfig, progress: Progress) -> Anonymized:
        return anonymize(data, cfg, progress, app.state.ner, app.state.ocr)

    return run


async def _purge_loop(jobs: JobRegistry, every_s: float = PURGE_EVERY_S) -> None:
    """Expired documents leave memory even when nobody asks for them."""
    while True:
        await asyncio.sleep(every_s)
        jobs.purge()


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
    app.add_middleware(
        UploadLimitMiddleware,
        max_bytes=int(settings.max_upload_mb * 1024 * 1024) + UPLOAD_OVERHEAD,
    )
    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(catalog.router)
    app.include_router(anonymization.router)
    return app


def build_app() -> FastAPI:
    """Production entry point: logging first, then the app."""
    settings = get_settings()
    setup_logging(settings)
    return create_app(settings)


app = build_app()
