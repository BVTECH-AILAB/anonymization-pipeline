"""Asynchronous jobs: in-memory queue, one worker, progress per phase.

A job holds the uploaded bytes until its turn, then the anonymization runs in a thread. The bytes
are released as soon as the run ends; a released document is kept in memory until it expires
(``ANONPIP_JOB_TTL_S``) or is deleted, and a blocked one keeps no file at all. When too many
finished jobs are kept, the oldest go first; only waiting and running jobs can make an upload
fail with 429. No persistence: a restart loses the jobs, and a job is visible only on the replica
that received it.

Events carry the first 8 characters of the job id: the full id is the only key to the document.
"""

import asyncio
import contextlib
import logging
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Literal

from fastapi import Depends, Request

from anonymization_pipeline.config import Settings
from anonymization_pipeline.ingest import DocumentError
from anonymization_pipeline.logging_setup import log_event
from anonymization_pipeline.pipeline_config import PipelineConfig
from anonymization_pipeline.services.anonymization import Anonymized, anonymize

logger = logging.getLogger(__name__)

JobStatus = Literal["queued", "running", "done", "blocked", "failed", "cancelled"]
TERMINAL: frozenset[str] = frozenset({"done", "blocked", "failed", "cancelled"})
GENERIC_ERROR = "Elaborazione non riuscita. Riprova più tardi."


class QueueFull(RuntimeError):
    """Too many jobs waiting or running."""


class Cancelled(Exception):
    """Raised inside the run when the job was cancelled."""


@dataclass
class Job:
    id: str
    cfg: PipelineConfig = field(repr=False)
    data: bytes | None = field(default=None, repr=False)
    created: float = field(default_factory=time.time)
    status: JobStatus = "queued"
    phase: str | None = None
    started: float | None = None
    finished: float | None = None
    expires: float | None = None
    error: str | None = None  # Italian message for failed jobs, never document content
    result: Anonymized | None = field(default=None, repr=False)
    cancel_requested: threading.Event = field(
        default_factory=threading.Event, repr=False
    )
    version: int = 0
    changed: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    @property
    def short(self) -> str:
        return self.id[:8]

    def touch(self) -> None:
        """Wake up whoever follows the job (SSE)."""
        self.version += 1
        self.changed.set()
        self.changed = asyncio.Event()


Runner = Callable[[bytes, PipelineConfig, Callable[[str], None]], Anonymized]


class JobRegistry:
    """FIFO queue and one worker task that runs the anonymization in a thread.

    Args:
        settings: Limits and time to live of the jobs.
        run: The anonymization of one document (tests replace it).
    """

    def __init__(self, settings: Settings, run: Runner = anonymize) -> None:
        self._run = run
        self._max_queued = settings.max_queued_jobs
        self._max_kept = settings.max_kept_jobs
        self._ttl = settings.job_ttl_s
        self._jobs: dict[str, Job] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._worker = asyncio.create_task(self._loop(), name="jobs")

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None
        for job in self._jobs.values():
            job.cancel_requested.set()
            job.data, job.result = None, None
        self._jobs.clear()

    # --- used by the API -----------------------------------------------------------------------

    def add(self, data: bytes, cfg: PipelineConfig) -> Job:
        """Queue a document.

        Raises:
            QueueFull: Too many jobs waiting or running.
        """
        self.purge()
        active = sum(1 for j in self._jobs.values() if j.status not in TERMINAL)
        if active >= self._max_queued:
            raise QueueFull("too many jobs waiting or running")
        job = Job(id=secrets.token_urlsafe(16), cfg=cfg, data=data)
        self._jobs[job.id] = job
        self._queue.put_nowait(job.id)
        log_event(
            logger, "job_queued", {"job": job.short, "size_kb": len(data) // 1024}
        )
        return job

    def get(self, job_id: str) -> Job | None:
        self.purge()
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        """Stop a waiting or running job at its next phase; a finished job is left as it is.

        Returns:
            True if the job was waiting or running.
        """
        job = self._jobs.get(job_id)
        if job is None or job.status in TERMINAL:
            return False
        job.cancel_requested.set()
        if job.status == "queued":
            self._finish(job, "cancelled")
        return True

    def delete(self, job_id: str, reason: str = "user") -> bool:
        """Remove the job and its document now."""
        job = self._jobs.pop(job_id, None)
        if job is None:
            return False
        job.cancel_requested.set()
        if job.status not in TERMINAL:
            job.status = "cancelled"
        job.data, job.result = None, None
        job.touch()
        log_event(logger, "job_deleted", {"job": job.short, "reason": reason})
        return True

    def purge(self) -> None:
        """Delete expired jobs, then the oldest finished ones over the limit."""
        now = time.time()
        for job in [j for j in self._jobs.values() if j.expires and j.expires < now]:
            self.delete(job.id, "expired")
        finished = sorted(
            (j for j in self._jobs.values() if j.status in TERMINAL),
            key=lambda j: j.finished or 0,
        )
        for job in finished[: max(0, len(finished) - self._max_kept)]:
            self.delete(job.id, "evicted")

    async def wait(self, job: Job, version: int, timeout: float) -> bool:
        """Wait until the job changes after ``version``; False on timeout."""
        if job.version != version:
            return True
        try:
            await asyncio.wait_for(job.changed.wait(), timeout)
        except TimeoutError:
            return False
        return True

    # --- internal ------------------------------------------------------------------------------

    def _finish(self, job: Job, status: JobStatus) -> None:
        job.status = status
        job.data = None
        job.finished = time.time()
        job.expires = job.finished + self._ttl
        job.touch()
        duration = round(job.finished - (job.started or job.created), 2)
        log_event(
            logger,
            "job_finished",
            {"job": job.short, "status": status, "duration_s": duration},
        )

    async def _loop(self) -> None:
        while True:
            job_id = await self._queue.get()
            job = self._jobs.get(job_id)
            if job is None or job.status != "queued":
                continue  # cancelled or deleted while waiting
            await self._process(job)

    async def _process(self, job: Job) -> None:
        loop = asyncio.get_running_loop()
        job.status, job.started = "running", time.time()
        job.touch()

        def progress(phase: str) -> None:  # runs in the worker thread
            if job.cancel_requested.is_set():
                raise Cancelled
            loop.call_soon_threadsafe(self._phase, job, phase)

        data = job.data or b""
        try:
            result = await asyncio.to_thread(self._run, data, job.cfg, progress)
        except Cancelled:
            self._finish(job, "cancelled")
            log_event(logger, "job_cancelled", {"job": job.short})
            return
        except DocumentError as exc:  # Italian message without document content
            job.error = str(exc)
            self._finish(job, "failed")
            return
        except Exception as exc:  # the message may hold document content: type only
            logger.error("Job %s failed: %s", job.short, type(exc).__name__)
            job.error = GENERIC_ERROR
            self._finish(job, "failed")
            return
        if job.id not in self._jobs or job.cancel_requested.is_set():
            return  # deleted or cancelled while the last phase ran: discard the result
        job.result = result
        self._finish(job, result.status)
        if result.status == "done":
            log_event(
                logger,
                "document_anonymized",
                {
                    "job": job.short,
                    "kind": result.kind,
                    "pages": result.pages,
                    "redacted_pages": len(result.strategies),
                    "boxes": result.boxes,
                    "counts": result.counts,
                    "total_s": result.timings.get("total"),
                },
            )
        else:
            log_event(
                logger,
                "document_rejected",
                {
                    "job": job.short,
                    "reason": result.reason,
                    "checks": sorted({f.check for f in result.failures}),
                    "scanned_pages": len(result.scanned_pages),
                },
            )

    def _phase(self, job: Job, phase: str) -> None:
        if job.status == "running":
            job.phase = phase
            job.touch()


def get_job_registry(request: Request) -> JobRegistry:
    """FastAPI dependency: the registry built in the lifespan."""
    return request.app.state.jobs


JobRegistryDep = Annotated[JobRegistry, Depends(get_job_registry)]
