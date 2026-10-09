"""Asynchronous jobs: in-memory queue, worker tasks, progress per phase.

A job receives the file, waits in the queue for its turn (the same slots as the synchronous
requests: the GPU is the bottleneck), then runs the pipeline. Its status can be read while it
runs: current phase, verification round, VLM calls done (from the metrics collector). The file
bytes and the key are released at the end of the processing; the result stays in memory until it
expires (``ANONPIP_JOB_TTL_S``) or is deleted. No persistence: a restart loses the jobs, and with
more than one replica a job is visible only on the pod that received it.
"""

import asyncio
import contextlib
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from fastapi import Depends, Request

from anonymization_pipeline.domain import Result
from anonymization_pipeline.errors import ServiceError, UnredactableContentError
from anonymization_pipeline.logging_setup import log_event
from anonymization_pipeline.metrics import Metrics
from anonymization_pipeline.pipeline import Progress
from anonymization_pipeline.pipeline_config import PipelineConfig
from anonymization_pipeline.redaction.vault import sha256

logger = logging.getLogger(__name__)

JobStatus = Literal["queued", "running", "completed", "rejected", "error", "cancelled"]


class QueueFull(RuntimeError):
    """Too many jobs queued or kept in memory."""


@dataclass
class Job:
    id: str
    name: str
    cfg: PipelineConfig = field(repr=False)
    data: bytes | None = field(default=None, repr=False)
    key: str | None = field(default=None, repr=False)
    created: float = field(default_factory=time.time)
    status: JobStatus = "queued"
    phase: str | None = None
    started: float | None = None
    finished: float | None = None
    expires: float | None = None
    reason: str | None = None
    residuals: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    result: Result | None = field(default=None, repr=False)
    collector: Metrics | None = field(default=None, repr=False)

    def as_dict(self, position: int | None = None) -> dict[str, Any]:
        """Public view of the job (no file content, no key)."""
        end = self.finished or time.time()
        data: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "phase": self.phase,
            "vlm_calls": len(self.collector.calls) if self.collector else 0,
            "created": self.created,
            "started": self.started,
            "finished": self.finished,
            "duration_s": round(end - self.started, 1) if self.started else None,
            "position": position,
            "expires": self.expires,
        }
        if self.status == "rejected":
            data.update(reason=self.reason, residuals=self.residuals)
        if self.status == "error":
            data["error"] = self.error
        return data


Runner = Callable[[Job, Progress], Awaitable[Result]]


class JobRegistry:
    """FIFO queue and ``workers`` tasks that run ``run(job, progress)``.

    Args:
        run: Runs one job (the runner takes a processing slot).
        workers: Worker tasks.
        max_jobs: Jobs kept in memory (queued, running and unexpired results).
        max_queued: Jobs waiting in the queue.
        ttl_s: Seconds a finished job stays available.
    """

    def __init__(
        self, run: Runner, workers: int, max_jobs: int, max_queued: int, ttl_s: float
    ) -> None:
        self._run = run
        self._workers_n = workers
        self._max_jobs, self._max_queued, self._ttl = max_jobs, max_queued, ttl_s
        self._jobs: dict[str, Job] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        self._workers = [
            asyncio.create_task(self._loop(), name=f"jobs-{i}")
            for i in range(self._workers_n)
        ]

    async def stop(self) -> None:
        for worker in self._workers:
            worker.cancel()
        for worker in self._workers:
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        self._workers = []

    # --- used by the API -----------------------------------------------------------------------

    def add(self, name: str, data: bytes, cfg: PipelineConfig, key: str | None) -> Job:
        """Queue a document.

        Raises:
            QueueFull: Too many jobs queued or in memory.
        """
        self._purge()
        queued = sum(1 for j in self._jobs.values() if j.status == "queued")
        if queued >= self._max_queued or len(self._jobs) >= self._max_jobs:
            raise QueueFull("too many jobs queued or in memory")
        job = Job(id=secrets.token_urlsafe(16), name=name, cfg=cfg, data=data, key=key)
        self._jobs[job.id] = job
        self._queue.put_nowait(job.id)
        log_event(
            logger, "job_queued", {"job": job.id[:8], "document": sha256(data)[:12]}
        )
        return job

    def get(self, job_id: str) -> Job | None:
        self._purge()
        return self._jobs.get(job_id)

    def position(self, job: Job) -> int | None:
        """1-based position in the queue (None when not queued)."""
        if job.status != "queued":
            return None
        return sum(
            1
            for j in self._jobs.values()
            if j.status == "queued" and j.created <= job.created
        )

    def list(self) -> list[Job]:
        self._purge()
        return sorted(self._jobs.values(), key=lambda j: j.created)

    def delete(self, job_id: str) -> bool:
        """Remove the job: if queued it will not run; if running its result is discarded."""
        job = self._jobs.pop(job_id, None)
        if job is None:
            return False
        if job.status in ("queued", "running"):
            job.status = "cancelled"
        job.data = job.key = None
        job.result = None
        return True

    # --- internal ------------------------------------------------------------------------------

    def _purge(self) -> None:
        now = time.time()
        for job_id in [
            i for i, j in self._jobs.items() if j.expires and j.expires < now
        ]:
            del self._jobs[job_id]

    async def _loop(self) -> None:
        while True:
            job_id = await self._queue.get()
            job = self._jobs.get(job_id)
            if job is None or job.status != "queued":
                continue  # deleted while queued
            await self._process(job)

    async def _process(self, job: Job) -> None:
        job.status, job.started = "running", time.time()

        def progress(phase: str, details: dict[str, Any]) -> None:
            job.phase = phase
            if details.get("metrics") is not None:
                job.collector = details["metrics"]

        status: JobStatus = "completed"
        result = None
        try:
            result = await self._run(job, progress)
        except UnredactableContentError as exc:
            status = "rejected"
            job.reason, job.residuals = (
                exc.extra.get("reason"),
                exc.extra.get("residuals", []),
            )
        except ServiceError as exc:
            status = "error"
            job.error = exc.user_message
        except (
            Exception
        ):  # unexpected: the message never reaches the client (it may hold data)
            logger.exception("Job %s: unexpected error", job.id[:8])
            status = "error"
            job.error = ServiceError.user_message
        job.data = job.key = None
        job.finished = time.time()
        job.expires = job.finished + self._ttl
        if job.status == "cancelled":  # deleted while running: the result is discarded
            return
        job.status, job.result = status, result
        log_event(
            logger,
            "job_finished",
            {
                "job": job.id[:8],
                "status": status,
                "duration_s": round(job.finished - job.started, 1),
            },
        )


def get_job_registry(request: Request) -> JobRegistry:
    """FastAPI dependency: the registry built in the lifespan."""
    return request.app.state.jobs


JobRegistryDep = Annotated[JobRegistry, Depends(get_job_registry)]
