"""Application factory and ASGI entry point (``anonymization_pipeline.main:app``)."""

import asyncio
import functools
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from anonymization_pipeline import __version__
from anonymization_pipeline.api import anonymization, catalog, health, jobs
from anonymization_pipeline.config import Settings, get_settings
from anonymization_pipeline.errors import register_exception_handlers
from anonymization_pipeline.logging_setup import setup_logging
from anonymization_pipeline.middleware import RequestIdMiddleware, UploadLimitMiddleware
from anonymization_pipeline.pipeline_config import PHASES
from anonymization_pipeline.services.anonymization import (
    AnonymizationService,
    ClientFactory,
)
from anonymization_pipeline.services.jobs import JobRegistry
from anonymization_pipeline.vlm import create_client

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create shared resources on startup and release them on shutdown.

    Clients (httpx clients ...) live on ``app.state``, never in module-level
    globals. An invalid pipeline configuration stops the startup: better than
    failing every request.
    """
    settings: Settings = app.state.settings
    http = httpx.AsyncClient()
    factory: ClientFactory = app.state.vlm_client_factory or functools.partial(
        create_client, http=http
    )
    slots = asyncio.Semaphore(settings.max_concurrent_jobs)
    service = AnonymizationService(settings, factory, slots)
    registry = JobRegistry(
        service.run_job,
        workers=settings.max_concurrent_jobs,
        max_jobs=settings.max_jobs,
        max_queued=settings.max_queued_jobs,
        ttl_s=settings.job_ttl_s,
    )
    app.state.http = http
    app.state.anonymization = service
    app.state.jobs = registry
    await registry.start()
    logger.info(
        "Service started: name=%s version=%s environment=%s models=%s",
        settings.app_name,
        __version__,
        settings.environment,
        ",".join(f"{p}:{service.config.model_for(p)[0]}" for p in PHASES),
    )
    for name in settings.obsolete():
        logger.warning(
            "Obsolete setting %s ignored: the VLMs are chosen in the models file "
            "(ANONPIP_MODELS_FILE)",
            name,
        )
    try:
        yield
    finally:
        await registry.stop()
        await http.aclose()
        logger.info("Service stopped")


def create_app(
    settings: Settings | None = None, vlm_client_factory: ClientFactory | None = None
) -> FastAPI:
    """Build the FastAPI application.

    Logging is configured by the caller (``build_app`` in production, the
    ``log_stream`` fixture in tests), not here.

    Args:
        settings: Explicit settings (tests). Defaults to ``get_settings()``.
        vlm_client_factory: Builds the VLM client of one run (tests use a fake).
            Defaults to the configured backend on the shared HTTP pool.
    """
    settings = settings or get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="Anonymization of PDF, images and text with a local VLM, docling for native PDFs "
        "and detectors in union (rules, labels, NER, VLM). Output: redacted Markdown, report, "
        "metrics and encrypted map. /jobs for asynchronous processing (used by the web UI).",
        lifespan=lifespan,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )
    app.state.settings = settings
    app.state.vlm_client_factory = vlm_client_factory
    # the restore endpoints receive two files: twice the limit, plus the multipart overhead
    max_bytes = int(2 * settings.max_upload_mb * 1024 * 1024) + 1024 * 1024
    app.add_middleware(UploadLimitMiddleware, max_bytes=max_bytes)
    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(anonymization.router)
    app.include_router(jobs.router)
    app.include_router(catalog.router)
    return app


def build_app() -> FastAPI:
    """Production entry point: logging first, then the app."""
    settings = get_settings()
    setup_logging(settings)
    return create_app(settings)


app = build_app()
