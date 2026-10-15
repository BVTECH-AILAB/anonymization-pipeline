"""Application factory and ASGI entry point (``anonmyzation_pipeline.main:app``)."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from anonmyzation_pipeline import __version__
from anonmyzation_pipeline.api import anonymization, health, web
from anonmyzation_pipeline.config import Settings, get_settings
from anonmyzation_pipeline.errors import register_exception_handlers
from anonmyzation_pipeline.logging_setup import setup_logging
from anonmyzation_pipeline.middleware import RequestIdMiddleware
from anonmyzation_pipeline.services.anonymization.detect.ner import GlinerNER
from anonmyzation_pipeline.services.anonymization.ocr import DoctrOCR
from anonmyzation_pipeline.services.anonymization.pipeline import Pipeline
from anonmyzation_pipeline.services.jobs import JobManager

logger = logging.getLogger(__name__)

PURGE_INTERVAL_SECONDS = 60


def build_pipeline(settings: Settings) -> Pipeline:
    """OCR and NER engines from the settings. No weights are loaded here."""
    ocr = DoctrOCR(
        det_arch=settings.ocr_det_arch,
        reco_arch=settings.ocr_reco_arch,
        dpi=settings.ocr_dpi,
        margin_pt=settings.ocr_margin_pt,
        device=settings.device,
    )
    ner = (
        GlinerNER(model_id=settings.ner_model, device=settings.device)
        if settings.ner_enabled
        else None
    )
    return Pipeline(
        ocr,
        ner,
        policy=settings.redaction_policy,
        ner_threshold=settings.ner_threshold,
        person_threshold=settings.ner_person_threshold,
    )


def load_models(pipeline: Pipeline) -> None:
    """Load the model weights. A failure leaves /readyz at 503 (fail-closed)."""
    try:
        pipeline.ocr.load()
        if pipeline.ner is not None:
            pipeline.ner.load()
    except Exception as exc:
        logger.error("Model loading failed: %s", type(exc).__name__)


async def _purge_loop(jobs: JobManager) -> None:
    """Delete expired documents periodically."""
    while True:
        await asyncio.sleep(PURGE_INTERVAL_SECONDS)
        jobs.purge_expired()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create shared resources on startup and release them on shutdown.

    The pipeline and the job manager live on ``app.state``, never in
    module-level globals. Models load in the background: the API answers
    the probes at once and ``/readyz`` turns ready when the weights are loaded.
    """
    settings: Settings = app.state.settings
    pipeline = build_pipeline(settings)
    jobs = JobManager(
        pipeline, settings, Path(settings.work_dir) if settings.work_dir else None
    )
    app.state.pipeline = pipeline
    app.state.jobs = jobs
    tasks = [asyncio.create_task(_purge_loop(jobs))]
    if settings.preload_models:
        tasks.append(asyncio.create_task(asyncio.to_thread(load_models, pipeline)))
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
    app.include_router(web.router)
    return app


def build_app() -> FastAPI:
    """Production entry point: logging first, then the app."""
    settings = get_settings()
    setup_logging(settings)
    return create_app(settings)


app = build_app()
