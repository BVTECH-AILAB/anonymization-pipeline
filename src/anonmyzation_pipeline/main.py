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
from anonmyzation_pipeline.services.anonymization.detect.ner import (
    CombinedNER,
    Gliner2NER,
    GlinerNER,
    NerEngine,
)
from anonmyzation_pipeline.services.anonymization.pipeline import Pipeline
from anonmyzation_pipeline.services.anonymization.reader import (
    LiteparseReader,
    TextReader,
)
from anonmyzation_pipeline.services.jobs import JobManager

logger = logging.getLogger(__name__)

PURGE_INTERVAL_SECONDS = 60
MODEL_RETRY_SECONDS = 30


def build_reader(settings: Settings) -> TextReader:
    """The text reader: LiteParse plus the OCR server. Nothing is loaded here."""
    return LiteparseReader(
        settings.ocr_server_url,
        dpi=settings.ocr_dpi,
        native=settings.native_text,
        picture_ocr=settings.picture_ocr,
    )


def build_pipeline(settings: Settings) -> Pipeline:
    """Text reader and NER engines from the settings. No weights are loaded here."""
    ner: NerEngine | None = None
    if settings.ner_enabled:
        # the models keep everything above the lower threshold; the pipeline applies both
        threshold = min(settings.ner_threshold, settings.ner_person_threshold)
        ner = GlinerNER(settings.ner_model, threshold, device=settings.device)
        if settings.ner2_enabled:
            second = Gliner2NER(settings.ner2_model, threshold, device=settings.device)
            ner = CombinedNER(ner, second)
    return Pipeline(
        build_reader(settings),
        ner,
        policy=settings.redaction_policy,
        ner_threshold=settings.ner_threshold,
        person_threshold=settings.ner_person_threshold,
    )


def load_models(pipeline: Pipeline) -> bool:
    """Load every engine that is not loaded yet; each one independently.

    A failure leaves ``/readyz`` at 503 (fail-closed) and is retried by the
    lifespan, so a model server that starts after the pod is picked up.

    Returns:
        True when every engine is ready.
    """
    engines = [("reader", pipeline.reader), ("ner", pipeline.ner)]
    for name, engine in engines:
        if engine is None or engine.ready:
            continue
        try:
            engine.load()
        except Exception as exc:
            logger.error(
                "Model loading failed: engine=%s error=%s", name, type(exc).__name__
            )
    return pipeline.ready


async def _load_loop(pipeline: Pipeline) -> None:
    """Load the models, retrying until every engine is ready."""
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
    pipeline = build_pipeline(settings)
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
