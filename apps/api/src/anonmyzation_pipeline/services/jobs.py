"""Anonymization jobs: upload -> analysis -> release, around the engine.

The engine (``anonymization_core``) converts, analyses, releases and edits a
document; this module owns what a service adds: the queue, progress over
Server-Sent Events, expiry, limits and the KPI events. Two modes: ``accurate`` releases the redacted PDF (and a Word copy) and can be
edited by hand; ``fast`` skips the visual detectors and the PDF redaction and
releases only the anonymized text as Markdown.

One job runs at a time (single worker: the models share one GPU). Progress
is pushed to Server-Sent Events subscribers. Documents live in a private
work folder only while the job exists and are deleted after
``job_ttl_minutes`` or on request. Nothing about the content (text, values,
file names) is ever logged: events carry only ids, counters and timings.
"""

import asyncio
import logging
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal

from anonymization_core.convert import upload_to_pdf
from anonymization_core.document import (
    apply_edits,
    display_boxes,
    release_markdown,
    release_pdf,
    render_png,
)
from anonymization_core.errors import AnonymizationError
from anonymization_core.pipeline import Analysis, Pipeline
from anonymization_core.taxonomy import LABELS
from fastapi import Depends, Request

from anonmyzation_pipeline.config import Settings
from anonmyzation_pipeline.errors import (
    FileTooLargeError,
    JobNotFoundError,
    JobNotReadyError,
    ModeNotSupportedError,
    ServiceError,
    ServiceUnavailableError,
    TooManyJobsError,
    service_error,
)
from anonmyzation_pipeline.logging_setup import log_event

logger = logging.getLogger(__name__)

# Share of the progress bar covered by each step.
WEIGHTS = {
    "read": (0, 45),
    "detect": (45, 80),
    "redact": (80, 90),
    "preview": (90, 100),
    "export": (80, 100),  # fast mode: Markdown instead of redaction and previews
}
TERMINAL = {"done", "error", "cancelled"}
Status = Literal["queued", "running", "done", "error", "cancelled"]
Mode = Literal["fast", "accurate"]


class Cancelled(Exception):
    """Raised inside the worker when the user cancels the job."""


@dataclass
class Job:
    """In-memory state of one document. ``name`` is shown to the uploader only, never logged."""

    id: str
    dir: Path
    name: str
    size: int
    pages: int
    source_format: str
    mode: Mode = "accurate"
    created: float = field(default_factory=time.monotonic)
    status: Status = "queued"
    step: str = "read"
    percent: int = 0
    error_code: str | None = None
    edits: int = 0
    # edits submitted and not finished: the released files are being replaced
    pending_edits: int = 0
    verified: bool = False
    analysis: Analysis | None = None
    result: dict[str, Any] | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = field(
        default_factory=list
    )

    @property
    def original(self) -> Path:
        return self.dir / "original.pdf"

    @property
    def anonymized(self) -> Path:
        return self.dir / "anonymized.pdf"

    @property
    def docx(self) -> Path:
        return self.dir / "anonymized.docx"

    @property
    def markdown(self) -> Path:
        return self.dir / "anonymized.md"

    def snapshot(self) -> dict[str, Any]:
        """Public state: what the status endpoint and the SSE stream return."""
        out: dict[str, Any] = {
            "id": self.id,
            "mode": self.mode,
            "status": self.status,
            "step": self.step,
            "percent": self.percent,
            "error_code": self.error_code,
        }
        if self.status == "done":
            out["result"] = self.result
        return out


class JobManager:
    """Creates, runs, edits and expires anonymization jobs.

    Args:
        pipeline: Analysis pipeline with its OCR and NER engines.
        settings: Service settings (limits, policy, verification).
        work_dir: Private folder for the documents; a temp folder when None.
    """

    def __init__(
        self, pipeline: Pipeline, settings: Settings, work_dir: Path | None = None
    ) -> None:
        self.pipeline = pipeline
        self.settings = settings
        self._owns_dir = work_dir is None
        self.work_dir = work_dir or Path(tempfile.mkdtemp(prefix="anonpip-"))
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, Job] = {}
        # the worker thread, request threads and the purge task all touch ``jobs``
        self._jobs_lock = threading.RLock()
        # One worker: one document at a time on the GPU, edits included.
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="anonymizer"
        )

    # -- lifecycle -----------------------------------------------------------
    def create(self, data: bytes, filename: str, mode: Mode = "accurate") -> Job:
        """Validate and store an upload, then queue it in ``mode``.

        Raises:
            FileTooLargeError, UnsupportedFormatError, UnreadableDocumentError,
            PasswordProtectedError, TooManyPagesError, TooManyJobsError,
            ServiceUnavailableError (models still loading at startup).
        """
        if self.settings.preload_models and not self.pipeline.ready:
            raise ServiceUnavailableError()
        if len(data) > self.settings.max_upload_mb * 1024 * 1024:
            raise FileTooLargeError()
        with self._jobs_lock:
            if len(self.jobs) >= self.settings.max_jobs:
                self.purge_expired()
                if len(self.jobs) >= self.settings.max_jobs:
                    raise TooManyJobsError()
        suffix = Path(filename).suffix.lower()
        jid = uuid.uuid4().hex
        job_dir = self.work_dir / jid
        job_dir.mkdir(mode=0o700)
        try:
            pages = upload_to_pdf(
                data,
                suffix,
                job_dir / "original.pdf",
                self.settings.max_pages,
                self.settings.ocr_dpi,
            )
        except AnonymizationError as exc:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise service_error(exc) from exc
        except Exception:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        job = Job(jid, job_dir, filename, len(data), pages, suffix.lstrip("."), mode)
        with self._jobs_lock:
            if len(self.jobs) >= self.settings.max_jobs:  # filled while converting
                shutil.rmtree(job_dir, ignore_errors=True)
                raise TooManyJobsError()
            self.jobs[jid] = job
        log_event(
            logger,
            "anonymization_job_created",
            {
                "job_id": jid,
                "pages": pages,
                "size_kb": round(len(data) / 1024),
                "source_format": job.source_format,
                "mode": mode,
            },
        )
        self._executor.submit(self._run, job)
        return job

    def get(self, jid: str) -> Job:
        """Return a job.

        Raises:
            JobNotFoundError: Unknown or expired id.
        """
        with self._jobs_lock:
            job = self.jobs.get(jid)
        if job is None:
            raise JobNotFoundError()
        return job

    def ready_job(self, jid: str) -> Job:
        """Return a completed job.

        Raises:
            JobNotFoundError: Unknown or expired id.
            JobNotReadyError: Still running, failed or cancelled.
        """
        job = self.get(jid)
        if job.status != "done" or job.analysis is None:
            raise JobNotReadyError()
        return job

    def accurate_job(self, jid: str) -> Job:
        """Return a completed job of the accurate mode (redacted PDF, editable).

        Raises:
            JobNotFoundError: Unknown or expired id.
            JobNotReadyError: Still running, failed or cancelled.
            ModeNotSupportedError: A fast job (Markdown only).
        """
        job = self.ready_job(jid)
        if job.mode != "accurate":
            raise ModeNotSupportedError()
        return job

    def cancel(self, jid: str) -> None:
        """Ask the worker to stop the job at the next step; a finished job is left as it is."""
        job = self.get(jid)
        if job.status not in TERMINAL:
            job.cancel.set()

    def delete(self, jid: str) -> None:
        """Forget a job and delete its files now."""
        with self._jobs_lock:
            job = self.jobs.pop(jid, None)
        if job is not None:
            job.cancel.set()
            shutil.rmtree(job.dir, ignore_errors=True)
            log_event(
                logger, "anonymization_job_deleted", {"job_id": jid, "reason": "user"}
            )

    def purge_expired(self) -> int:
        """Delete jobs older than ``job_ttl_minutes``.

        Returns:
            Number of jobs deleted.
        """
        limit = time.monotonic() - self.settings.job_ttl_minutes * 60
        with self._jobs_lock:
            expired = [j for j in self.jobs.values() if j.created < limit]
            for job in expired:
                self.jobs.pop(job.id, None)
        for job in expired:
            job.cancel.set()
            shutil.rmtree(job.dir, ignore_errors=True)
            log_event(
                logger,
                "anonymization_job_deleted",
                {"job_id": job.id, "reason": "expired"},
            )
        return len(expired)

    def shutdown(self) -> None:
        """Stop the worker and delete every document."""
        with self._jobs_lock:
            jobs = list(self.jobs.values())
            self.jobs.clear()
        for job in jobs:
            job.cancel.set()
        self._executor.shutdown(wait=False, cancel_futures=True)
        for job in jobs:
            shutil.rmtree(job.dir, ignore_errors=True)
        if self._owns_dir:
            shutil.rmtree(self.work_dir, ignore_errors=True)

    # -- progress (SSE) ------------------------------------------------------
    def subscribe(self, job: Job) -> asyncio.Queue:
        """Register an asyncio queue that receives every state change of ``job``."""
        queue: asyncio.Queue = asyncio.Queue()
        job.subscribers.append((asyncio.get_running_loop(), queue))
        return queue

    def unsubscribe(self, job: Job, queue: asyncio.Queue) -> None:
        """Stop sending progress events to ``queue``."""
        job.subscribers[:] = [
            (loop, q) for loop, q in job.subscribers if q is not queue
        ]

    def _publish(self, job: Job) -> None:
        """Push the job state to every subscriber (called from the worker thread)."""
        snapshot = job.snapshot()
        for loop, queue in list(job.subscribers):
            if not loop.is_closed():
                loop.call_soon_threadsafe(queue.put_nowait, snapshot)

    def _progress(self, job: Job) -> Callable[[str, float], None]:
        def report(step: str, fraction: float) -> None:
            if job.cancel.is_set():
                raise Cancelled()
            lo, hi = WEIGHTS[step]
            pct = int(lo + (hi - lo) * max(0.0, min(1.0, fraction)))
            if (step, pct) != (job.step, job.percent):
                job.step, job.percent = step, pct
                self._publish(job)

        return report

    # -- work ----------------------------------------------------------------
    def _run(self, job: Job) -> None:
        """Worker: analysis, then redaction, verification and exports."""
        t0 = time.perf_counter()
        try:
            job.status = "running"
            self._publish(job)
            analysis = self.pipeline.analyze(
                job.original,
                progress=self._progress(job),
                visual=job.mode == "accurate",
            )
            job.analysis = analysis
            timings = {
                "text_layers": analysis.timings["text_layers"],
                "detection": analysis.timings["detection"],
                "visual": analysis.timings["visual"],
            }
            if job.mode == "fast":
                self._finish_fast(job, t0, timings)
            else:
                self._finish(job, t0, timings)
            log_event(logger, "anonymization_completed", self._event_payload(job))
        except Cancelled:
            job.status = "cancelled"
            self._publish(job)
            shutil.rmtree(job.dir, ignore_errors=True)
            with self._jobs_lock:
                self.jobs.pop(job.id, None)
            log_event(logger, "anonymization_cancelled", {"job_id": job.id})
        except Exception as exc:
            # Only the exception type: messages can contain document text.
            logger.error(
                "Anonymization failed: job_id=%s error=%s", job.id, type(exc).__name__
            )
            # a known cause (NER or OCR down) keeps its code: the client can retry later
            error = service_error(exc) if isinstance(exc, AnonymizationError) else exc
            known = isinstance(error, ServiceError) and type(error) is not ServiceError
            code = error.code if known else "PROCESSING_FAILED"
            job.status, job.error_code = "error", code
            self._publish(job)
            log_event(
                logger,
                "anonymization_failed",
                {"job_id": job.id, "error": type(exc).__name__},
            )

    def _finish(
        self,
        job: Job,
        t_start: float,
        timings: dict[str, float],
        edited: bool = False,
    ) -> None:
        """Redact, verify, export; shared by the first run and every edit.

        Fail-closed: nothing is downloadable while a new version is produced, and a
        version that fails verification replaces the files but stays blocked.
        """
        analysis = job.analysis
        if analysis is None:
            raise JobNotReadyError()
        job.verified = False
        release = release_pdf(
            job.original,
            analysis,
            job.anonymized,
            job.docx,
            style=self.settings.redaction_style,
            tol=self.settings.verify_pixel_tolerance,
            progress=self._progress(job),
        )
        if edited:
            # only now: a preview cached under the new count shows the new file
            job.edits += 1
        for cached in job.dir.glob("anonymized-*.png"):
            cached.unlink()
        timings = timings | release.seconds
        job.verified = release.verified
        job.result = self._result(job, analysis, t_start, timings, release.issues) | {
            "verified": release.verified,
            # pages released as images: a surgical check failed and the page was rendered
            "flattened_pages": [p + 1 for p in release.flattened],
            "boxes": display_boxes(analysis),
        }
        job.percent, job.status = 100, "done"
        self._publish(job)

    def _finish_fast(self, job: Job, t_start: float, timings: dict[str, float]) -> None:
        """Markdown export and its verification (fast mode); fail-closed as ``_finish``."""
        analysis = job.analysis
        if analysis is None:
            raise JobNotReadyError()
        release = release_markdown(analysis, job.markdown, self._progress(job))
        timings = timings | {"verification": release.seconds}
        job.verified = release.verified
        job.result = self._result(job, analysis, t_start, timings, release.issues)
        job.percent, job.status = 100, "done"
        self._publish(job)

    @staticmethod
    def _result(
        job: Job,
        analysis: Analysis,
        t_start: float,
        timings: dict[str, float],
        issues: list[str],
    ) -> dict[str, Any]:
        """Result fields shared by both modes."""
        total = round(time.perf_counter() - t_start, 2)
        return {
            "id": job.id,
            "mode": job.mode,
            "filename": job.name,
            "size": job.size,
            "pages": analysis.pages,
            "n_pages": len(analysis.pages),
            "seconds_total": total,
            "seconds_per_page": round(total / max(len(analysis.pages), 1), 2),
            "timings": timings,
            "counts": analysis.counts(),
            "verified": not issues,
            "issues": issues[:20],
            "edits": job.edits,
        }

    def _event_payload(self, job: Job) -> dict[str, Any]:
        """KPI payload: counters, ids and timings only."""
        result = job.result or {}
        analysis = job.analysis
        return {
            "job_id": job.id,
            "pages": result.get("n_pages", 0),
            "scanned_pages": sum(1 for p in analysis.pages if p["scanned"])
            if analysis
            else 0,
            "boxes": sum(1 for b in analysis.boxes if b.redact) if analysis else 0,
            "values_by_label": result.get("counts", {}),
            "verified": job.verified,
            "edits": job.edits,
            "seconds_total": result.get("seconds_total"),
            "timings": result.get("timings", {}),
            "mode": job.mode,
        }

    def edit(
        self,
        jid: str,
        add: list[tuple[int, tuple[float, float, float, float]]],
        remove: list[int],
        restore: list[int],
        search: str,
    ) -> dict[str, Any]:
        """Apply manual corrections, then redact and verify again (no new detection).

        Args:
            jid: Job id.
            add: ``(page, rect)`` areas to redact.
            remove: Box ids that must not be redacted.
            restore: Box ids to redact again.
            search: Text whose every occurrence is redacted ("" for none).

        Returns:
            The new result, with ``edit_seconds`` and ``search_hits``.
        """
        job = self.accurate_job(jid)
        with self._jobs_lock:
            job.pending_edits += 1
        try:
            future = self._executor.submit(
                self._apply_edit, job, add, remove, restore, search
            )
            return future.result()
        finally:
            with self._jobs_lock:
                job.pending_edits -= 1

    def _apply_edit(
        self,
        job: Job,
        add: list[tuple[int, tuple[float, float, float, float]]],
        remove: list[int],
        restore: list[int],
        search: str,
    ) -> dict[str, Any]:
        analysis = job.analysis
        if analysis is None or job.result is None:
            raise JobNotReadyError()
        t0 = time.perf_counter()
        outcome = apply_edits(analysis, add, remove, restore, search)
        timings = dict(job.result["timings"])
        try:
            self._finish(job, t0, timings, edited=True)
        except Cancelled:
            raise JobNotFoundError() from None  # deleted while the edit was running
        except Exception as exc:
            # nothing released (the engine leaves no partial file): the job stays blocked and says so
            job.result = job.result | {"verified": False}
            job.percent, job.status = 100, "done"
            self._publish(job)
            logger.error("Edit failed: job=%s error=%s", job.id, type(exc).__name__)
            raise
        result = job.result
        result["edit_seconds"] = round(time.perf_counter() - t0, 2)
        result["search_hits"] = outcome.search_hits
        log_event(
            logger,
            "anonymization_edited",
            {
                "job_id": job.id,
                "added": outcome.added,
                "removed": len(remove),
                "restored": len(restore),
                "search_hits": outcome.search_hits,
                "verified": job.verified,
                "seconds": result["edit_seconds"],
            },
        )
        return result

    def render_page(self, jid: str, which: str, page: int, dpi: int) -> Path:
        """PNG of one page of the original or the anonymized document (cached).

        Raises:
            JobNotFoundError: Unknown or expired id, no such page, or the job
                was deleted while the page was rendered.
        """
        job = self.accurate_job(jid) if which == "anonymized" else self.ready_job(jid)
        # the anonymized version changes with every edit: the edit count is in its name
        version = job.edits if which == "anonymized" else 0
        cache = job.dir / f"{which}-{page}-{dpi}-{version}.png"
        if cache.exists():
            return cache
        src = job.original if which == "original" else job.anonymized
        try:
            render_png(src, page, dpi, cache)
        except AnonymizationError as exc:
            raise service_error(exc) from exc
        except Exception:
            # PDFium fails on a source removed by expiry or deletion: not an error
            if job.dir.exists():
                raise
            raise JobNotFoundError() from None
        return cache

    def preview(self, jid: str) -> dict[str, Any]:
        """What the side-by-side review needs: page sizes, boxes, label names.

        Boxes are in display space, as drawn on the rendered pages, and carry
        no text: the reviewer sees the value on the original page.

        Raises:
            JobNotFoundError, JobNotReadyError, ModeNotSupportedError.
        """
        job = self.accurate_job(jid)
        result = job.result
        if result is None:
            raise JobNotReadyError()
        return {
            "id": job.id,
            "mode": job.mode,
            "version": result["edits"],
            "verified": result["verified"],
            "pages": [
                {"index": i, "width": p["width"], "height": p["height"]}
                for i, p in enumerate(result["pages"])
            ],
            "boxes": [
                {k: b[k] for k in ("id", "page", "rect", "label", "layer", "redact")}
                for b in result["boxes"]
            ],
            "labels": {code: label.it for code, label in LABELS.items()},
        }


def get_job_manager(request: Request) -> JobManager:
    """FastAPI dependency. Unit tests override it with a manager built on fake engines."""
    return request.app.state.jobs


JobManagerDep = Annotated[JobManager, Depends(get_job_manager)]
