"""Anonymization jobs: upload -> analysis -> redaction -> verification -> exports.

One job runs at a time (single worker: the models share one GPU). Progress
is pushed to Server-Sent Events subscribers. Documents live in a private
work folder only while the job exists and are deleted after
``job_ttl_minutes`` or on request. Nothing about the content (text, values,
file names) is ever logged: events carry only ids, counters and timings.
"""

import asyncio
import io
import logging
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal

import pymupdf
from fastapi import Depends, Request
from PIL import Image, ImageOps

from anonmyzation_pipeline.config import Settings
from anonmyzation_pipeline.errors import (
    FileTooLargeError,
    JobNotFoundError,
    JobNotReadyError,
    PasswordProtectedError,
    ServiceUnavailableError,
    TooManyJobsError,
    TooManyPagesError,
    UnreadableDocumentError,
    UnsupportedFormatError,
)
from anonmyzation_pipeline.logging_setup import log_event
from anonmyzation_pipeline.services.anonymization.export_docx import export_docx
from anonmyzation_pipeline.services.anonymization.pipeline import (
    Analysis,
    Box,
    Pipeline,
)
from anonmyzation_pipeline.services.anonymization.redact import redact
from anonmyzation_pipeline.services.anonymization.verify import verify

logger = logging.getLogger(__name__)

PDF_SUFFIXES = {".pdf"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
# Share of the progress bar covered by each step.
WEIGHTS = {
    "read": (0, 45),
    "detect": (45, 80),
    "redact": (80, 90),
    "preview": (90, 100),
}
TERMINAL = {"done", "error", "cancelled"}
Status = Literal["queued", "running", "done", "error", "cancelled"]


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
    created: float = field(default_factory=time.monotonic)
    status: Status = "queued"
    step: str = "read"
    percent: int = 0
    error_code: str | None = None
    edits: int = 0
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

    def snapshot(self) -> dict[str, Any]:
        """Public state: what the status endpoint and the SSE stream return."""
        out: dict[str, Any] = {
            "id": self.id,
            "status": self.status,
            "step": self.step,
            "percent": self.percent,
            "error_code": self.error_code,
        }
        if self.status == "done":
            out["result"] = self.result
        return out


def to_pdf(data: bytes, suffix: str, dest: Path, max_pages: int) -> int:
    """Store the upload as a PDF; images become one page per frame.

    Args:
        data: Uploaded bytes.
        suffix: Lower-case file extension.
        dest: Where to write the PDF.
        max_pages: Page limit.

    Returns:
        Number of pages.

    Raises:
        UnsupportedFormatError: Unknown extension.
        UnreadableDocumentError: The file cannot be parsed.
        PasswordProtectedError: Encrypted PDF.
        TooManyPagesError: More pages than allowed.
    """
    if suffix in PDF_SUFFIXES:
        try:
            doc = pymupdf.open(stream=data, filetype="pdf")
        except Exception as exc:
            raise UnreadableDocumentError() from exc
        with doc:
            if doc.needs_pass:
                raise PasswordProtectedError()
            pages = len(doc)
        if pages == 0:
            raise UnreadableDocumentError()
        if pages > max_pages:
            raise TooManyPagesError()
        dest.write_bytes(data)
        return pages
    if suffix not in IMAGE_SUFFIXES:
        raise UnsupportedFormatError()
    try:
        img = Image.open(io.BytesIO(data))
        frames = []
        while True:
            # phone photos: apply the EXIF rotation before anything else
            frames.append(ImageOps.exif_transpose(img.copy()).convert("RGB"))
            if len(frames) > max_pages:
                raise TooManyPagesError()
            try:
                img.seek(img.tell() + 1)
            except EOFError:
                break
    except TooManyPagesError:
        raise
    except Exception as exc:
        raise UnreadableDocumentError() from exc
    with pymupdf.open() as doc:
        for frame in frames:
            dpi = (frame.info.get("dpi") or (200, 200))[0] or 200
            buf = io.BytesIO()
            frame.save(buf, "JPEG", quality=92)
            page = doc.new_page(
                width=frame.width * 72 / dpi, height=frame.height * 72 / dpi
            )
            page.insert_image(page.rect, stream=buf.getvalue())
        doc.save(dest)
    return len(frames)


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
        # One worker: one document at a time on the GPU, edits included.
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="anonymizer"
        )

    # -- lifecycle -----------------------------------------------------------
    def create(self, data: bytes, filename: str) -> Job:
        """Validate and store an upload, then queue it.

        Raises:
            FileTooLargeError, UnsupportedFormatError, UnreadableDocumentError,
            PasswordProtectedError, TooManyPagesError, TooManyJobsError,
            ServiceUnavailableError (models still loading at startup).
        """
        if self.settings.preload_models and not self.pipeline.ready:
            raise ServiceUnavailableError()
        if len(data) > self.settings.max_upload_mb * 1024 * 1024:
            raise FileTooLargeError()
        active = [j for j in self.jobs.values() if j.status not in TERMINAL]
        if (
            len(self.jobs) >= self.settings.max_jobs
            or len(active) >= self.settings.max_jobs
        ):
            self.purge_expired()
            if len(self.jobs) >= self.settings.max_jobs:
                raise TooManyJobsError()
        suffix = Path(filename).suffix.lower()
        jid = uuid.uuid4().hex
        job_dir = self.work_dir / jid
        job_dir.mkdir(mode=0o700)
        try:
            pages = to_pdf(
                data, suffix, job_dir / "original.pdf", self.settings.max_pages
            )
        except Exception:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        job = Job(jid, job_dir, filename, len(data), pages, suffix.lstrip("."))
        self.jobs[jid] = job
        log_event(
            logger,
            "anonymization_job_created",
            {
                "job_id": jid,
                "pages": pages,
                "size_kb": round(len(data) / 1024),
                "source_format": job.source_format,
            },
        )
        self._executor.submit(self._run, job)
        return job

    def get(self, jid: str) -> Job:
        """Return a job.

        Raises:
            JobNotFoundError: Unknown or expired id.
        """
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

    def cancel(self, jid: str) -> None:
        """Ask the worker to stop the job at the next step."""
        self.get(jid).cancel.set()

    def delete(self, jid: str) -> None:
        """Forget a job and delete its files now."""
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
        expired = [j for j in self.jobs.values() if j.created < limit]
        for job in expired:
            self.jobs.pop(job.id, None)
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
        for job in self.jobs.values():
            job.cancel.set()
        self._executor.shutdown(wait=False, cancel_futures=True)
        for job in list(self.jobs.values()):
            shutil.rmtree(job.dir, ignore_errors=True)
        self.jobs.clear()
        if self._owns_dir:
            shutil.rmtree(self.work_dir, ignore_errors=True)

    # -- progress (SSE) ------------------------------------------------------
    def subscribe(self, job: Job) -> asyncio.Queue:
        """Register an asyncio queue that receives every state change of ``job``."""
        queue: asyncio.Queue = asyncio.Queue()
        job.subscribers.append((asyncio.get_running_loop(), queue))
        return queue

    def unsubscribe(self, job: Job, queue: asyncio.Queue) -> None:
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
            analysis = self.pipeline.analyze(job.original, progress=self._progress(job))
            job.analysis = analysis
            timings = {
                "text_layers": analysis.timings["text_layers"],
                "detection": analysis.timings["detection"],
                "visual": analysis.timings["visual"],
            }
            self._finish(job, t0, timings)
            log_event(logger, "anonymization_completed", self._event_payload(job))
        except Cancelled:
            job.status = "cancelled"
            self._publish(job)
            shutil.rmtree(job.dir, ignore_errors=True)
            self.jobs.pop(job.id, None)
            log_event(logger, "anonymization_cancelled", {"job_id": job.id})
        except Exception as exc:
            # Only the exception type: messages can contain document text.
            logger.error(
                "Anonymization failed: job_id=%s error=%s", job.id, type(exc).__name__
            )
            job.status, job.error_code = "error", "PROCESSING_FAILED"
            self._publish(job)
            log_event(
                logger,
                "anonymization_failed",
                {"job_id": job.id, "error": type(exc).__name__},
            )

    def _finish(self, job: Job, t_start: float, timings: dict[str, float]) -> None:
        """Redact, verify, export; shared by the first run and every edit."""
        analysis = job.analysis
        if analysis is None:
            raise JobNotReadyError()
        report = self._progress(job)
        report("redact", 0)
        t1 = time.perf_counter()
        redact(
            job.original, analysis, job.anonymized, style=self.settings.redaction_style
        )
        t2 = time.perf_counter()
        report("preview", 0)
        rep = verify(
            job.original,
            job.anonymized,
            analysis,
            tol=self.settings.verify_pixel_tolerance,
        )
        t3 = time.perf_counter()
        export_docx(analysis, job.docx)
        for cached in job.dir.glob("anonymized-*.png"):
            cached.unlink()
        timings = timings | {
            "redaction": round(t2 - t1, 2),
            "verification": round(t3 - t2, 2),
        }
        total = round(time.perf_counter() - t_start, 2)
        job.verified = rep.ok
        job.result = {
            "id": job.id,
            "filename": job.name,
            "size": job.size,
            "pages": analysis.pages,
            "n_pages": len(analysis.pages),
            "seconds_total": total,
            "seconds_per_page": round(total / max(len(analysis.pages), 1), 2),
            "timings": timings,
            "counts": analysis.counts(),
            "verified": rep.ok,
            "issues": rep.issues[:20],
            "edits": job.edits,
            "boxes": [{"id": i, **asdict(b)} for i, b in enumerate(analysis.boxes)],
        }
        job.percent, job.status = 100, "done"
        self._publish(job)

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
        job = self.ready_job(jid)
        future = self._executor.submit(
            self._apply_edit, job, add, remove, restore, search
        )
        return future.result()

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
        n_pages = len(analysis.pages)
        for i in remove:
            if 0 <= i < len(analysis.boxes):
                analysis.boxes[i].redact = False
                analysis.boxes[i].sources = sorted(
                    set(analysis.boxes[i].sources) | {"removed-by-user"}
                )
        for i in restore:
            if 0 <= i < len(analysis.boxes):
                analysis.boxes[i].redact = True
        added = 0
        for page, rect in add:
            if 0 <= page < n_pages:
                snapped = analysis.snap(page, rect)
                analysis.boxes.append(
                    Box(
                        page,
                        snapped,
                        "PERSON",
                        "",
                        1.0,
                        ["manual-area"],
                        "manual",
                        True,
                    )
                )
                added += 1
        found = analysis.search(search) if search else []
        analysis.boxes += found
        job.edits += 1
        timings = dict(job.result["timings"])
        self._finish(job, t0, timings)
        result = job.result
        result["edit_seconds"] = round(time.perf_counter() - t0, 2)
        result["search_hits"] = len(found)
        log_event(
            logger,
            "anonymization_edited",
            {
                "job_id": job.id,
                "added": added,
                "removed": len(remove),
                "restored": len(restore),
                "search_hits": len(found),
                "verified": job.verified,
                "seconds": result["edit_seconds"],
            },
        )
        return result

    def render_page(self, jid: str, which: str, page: int, dpi: int) -> Path:
        """PNG of one page of the original or the anonymized document (cached)."""
        job = self.ready_job(jid)
        cache = job.dir / f"{which}-{page}-{dpi}.png"
        if not cache.exists():
            src = job.original if which == "original" else job.anonymized
            with pymupdf.open(src) as doc:
                if not 0 <= page < len(doc):
                    raise JobNotFoundError()
                doc[page].get_pixmap(dpi=dpi).save(cache)
        return cache


def get_job_manager(request: Request) -> JobManager:
    """FastAPI dependency. Unit tests override it with a manager built on fake engines."""
    return request.app.state.jobs


JobManagerDep = Annotated[JobManager, Depends(get_job_manager)]
