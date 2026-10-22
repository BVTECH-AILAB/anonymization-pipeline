"""Shared resources on ``app.state``: created at startup, released at shutdown."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from src import __version__
from src.config import Settings
from src.llms.ner import AilabNER
from src.services.anonymization import JobManager
from src.services.anonymizer.factory import build_pipeline, load_models
from src.services.anonymizer.pipeline import Pipeline

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
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
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
