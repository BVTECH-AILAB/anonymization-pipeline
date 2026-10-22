"""Anonymization API, shaped like the document agent of ai-platform-v2.

``POST /anonymizations/sessions`` uploads a document and creates a session;
``POST /sessions/{id}/anonymize/stream`` runs it and streams SSE frames
(``status``, then ``done`` or ``error``); ``POST /sessions/{id}/edits/stream``
applies manual corrections the same way. ``mode=accurate`` (default) releases
the redacted PDF and Word copy and supports the side-by-side review;
``mode=fast`` releases only the anonymized text as Markdown. URLs carry only
random session ids: never names, codici fiscali or file names.
"""

import asyncio
import os
import pathlib
import time
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import Future
from typing import Annotated, Any, BinaryIO, Literal
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Path, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse

from src.errors import (
    JobCancelledError,
    JobNotFoundError,
    JobNotReadyError,
    ModeNotSupportedError,
    ProcessingFailedError,
    ServiceError,
    ServiceUnavailableError,
    SessionBusyError,
    VerificationFailedError,
    error_for_code,
)
from src.models.anonymization import (
    AnonymizationErrorEvent,
    AnonymizationSessionResponse,
    AnonymizationTurnResponse,
    EditRequest,
    EditTurnResponse,
    LabelView,
    ReviewManifest,
    ReviewPage,
    StatusEvent,
)
from src.router.sse import KEEPALIVE, format_sse, sse_response
from src.services.anonymization import TERMINAL, Job, JobManager, Mode
from src.services.anonymizer.taxonomy import LABELS

router = APIRouter(prefix="/anonymizations", tags=["anonymization"])

SessionId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
Format = Literal["pdf", "docx", "md"]
# Generic download names: the original file name may contain the patient's name.
DOWNLOAD_NAMES = {
    "pdf": "documento_anonimizzato.pdf",
    "docx": "documento_anonimizzato.docx",
    "md": "documento_anonimizzato.md",
}
MEDIA_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "md": "text/markdown; charset=utf-8",
}
# the files each mode releases; the first is the default of download and preview
FORMATS: dict[str, tuple[Format, ...]] = {"accurate": ("pdf", "docx"), "fast": ("md",)}
STEP_TEXT = {
    "read": "Lettura del documento",
    "detect": "Ricerca dei dati personali",
    "redact": "Oscuramento dei dati",
    "preview": "Verifica del documento anonimizzato",
    "export": "Esportazione e verifica del testo",
}
SSE_KEEPALIVE_SECONDS = 15
CHUNK_BYTES = 64 * 1024


def _jobs(request: Request) -> JobManager:
    """The job manager of the app; 503 while it is not there."""
    jobs = getattr(request.app.state, "jobs", None)
    if jobs is None:
        raise ServiceUnavailableError()
    return jobs


def open_file_response(
    path: pathlib.Path,
    media_type: str,
    filename: str,
    disposition: Literal["attachment", "inline"] = "attachment",
) -> StreamingResponse:
    """Stream a job file from a handle opened now.

    The job can expire or be deleted at any moment: a file already gone is a
    404, and a file deleted while it is sent is still sent whole (the open
    handle keeps it).

    Raises:
        JobNotFoundError: The file no longer exists.
    """
    try:
        fh = path.open("rb")
    except FileNotFoundError:
        raise JobNotFoundError() from None
    headers = {
        "Cache-Control": "no-store",
        "Content-Length": str(os.fstat(fh.fileno()).st_size),
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(filename)}",
    }
    return StreamingResponse(_chunks(fh), media_type=media_type, headers=headers)


def _chunks(fh: BinaryIO) -> Iterator[bytes]:
    with fh:
        while chunk := fh.read(CHUNK_BYTES):
            yield chunk


def _session(job: Job) -> AnonymizationSessionResponse:
    state = job.snapshot()
    return AnonymizationSessionResponse(
        session_id=state.pop("id"), result=state.pop("result", None), **state
    )


def _elapsed_ms(t0: float) -> int:
    return round((time.perf_counter() - t0) * 1000)


def _status_frame(state: dict[str, Any]) -> str:
    text = "In attesa" if state["status"] == "queued" else STEP_TEXT[state["step"]]
    event = StatusEvent(step=state["step"], percent=state["percent"], text=text)
    return format_sse("status", event.model_dump_json())


def _error_frame(error: ServiceError) -> str:
    event = AnonymizationErrorEvent(
        error_title=error.user_title,
        error_message=error.user_message,
        error_code=error.code,
    )
    return format_sse("error", event.model_dump_json())


async def _single(frame: str) -> AsyncIterator[str]:
    yield frame


async def _anonymize_frames(
    request: Request, jobs: JobManager, job: Job, queue: asyncio.Queue, t0: float
) -> AsyncIterator[str]:
    """Progress until the job ends, then ``done`` or ``error``.

    A client that disconnects stops only its stream: the job goes on and
    ``GET /sessions/{id}`` reports it.
    """
    try:
        state = job.snapshot()
        while state["status"] not in TERMINAL:
            yield _status_frame(state)
            while True:
                if await request.is_disconnected():
                    return
                try:
                    state = await asyncio.wait_for(
                        queue.get(), timeout=SSE_KEEPALIVE_SECONDS
                    )
                    break
                except TimeoutError:
                    yield KEEPALIVE
        if state["status"] == "done":
            done = AnonymizationTurnResponse(
                session=_session(job), elapsed_ms=_elapsed_ms(t0)
            )
            yield format_sse("done", done.model_dump_json())
        elif state["status"] == "cancelled":
            yield _error_frame(JobCancelledError())
        else:
            yield _error_frame(error_for_code(state["error_code"]))
    finally:
        jobs.unsubscribe(job, queue)


async def _edit_frames(
    request: Request,
    jobs: JobManager,
    job: Job,
    queue: asyncio.Queue,
    future: Future[dict[str, Any]],
    t0: float,
) -> AsyncIterator[str]:
    """Progress of the redaction and verification, then ``done`` or ``error``."""
    edit = asyncio.wrap_future(future)
    getter: asyncio.Future | None = None
    try:
        while not edit.done():
            getter = asyncio.ensure_future(queue.get())
            done, _ = await asyncio.wait(
                {edit, getter},
                timeout=SSE_KEEPALIVE_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if getter in done:
                yield _status_frame(getter.result())
                continue
            getter.cancel()
            if not done:
                if await request.is_disconnected():
                    return
                yield KEEPALIVE
        try:
            result = edit.result()
        except ServiceError as exc:
            yield _error_frame(exc)
            return
        except Exception:
            # the job manager logged the type; the message may hold document text
            yield _error_frame(ProcessingFailedError())
            return
        turn = EditTurnResponse(
            session=_session(job),
            search_hits=result["search_hits"],
            edit_seconds=result["edit_seconds"],
            elapsed_ms=_elapsed_ms(t0),
        )
        yield format_sse("done", turn.model_dump_json())
    finally:
        if getter is not None:
            getter.cancel()
        jobs.unsubscribe(job, queue)


def _released(job: Job, fmt: Format | None) -> tuple[pathlib.Path, Format]:
    """The released file of a finished session in ``fmt`` (default: the mode's first).

    Fail-closed: refused while an edit replaces it and when verification failed.

    Raises:
        JobNotReadyError, ModeNotSupportedError, VerificationFailedError.
    """
    fmt = fmt or FORMATS[job.mode][0]
    if fmt not in FORMATS[job.mode]:
        raise ModeNotSupportedError()
    if job.pending_edits:
        raise JobNotReadyError()
    if not job.verified:
        raise VerificationFailedError()
    path = {"pdf": job.anonymized, "docx": job.docx, "md": job.markdown}[fmt]
    return path, fmt


@router.get("/labels")
async def labels() -> list[LabelView]:
    """PII taxonomy: Italian title, kind and default policy of every label."""
    return [
        LabelView(
            code=code,
            title=label.it,
            kind="visual" if label.kind == "visual" else "text",
            redact=label.redact,
        )
        for code, label in LABELS.items()
    ]


@router.post("/sessions", status_code=201)
async def create_session(
    request: Request,
    file: Annotated[UploadFile, File()],
    mode: Annotated[Mode, Form()] = "accurate",
) -> AnonymizationSessionResponse:
    """Upload a document (PDF, scanned PDF, JPG, PNG, TIFF) and create a session.

    ``accurate``: redacted PDF, reviewable and editable. ``fast``: anonymized text
    as Markdown. The work starts with ``POST /sessions/{id}/anonymize/stream``.
    """
    jobs = _jobs(request)
    # never more than the limit in memory: one byte over is enough to refuse it
    data = await file.read(jobs.settings.max_upload_mb * 1024 * 1024 + 1)
    job = await asyncio.to_thread(jobs.create, data, file.filename or "documento", mode)
    return _session(job)


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: SessionId, request: Request
) -> AnonymizationSessionResponse:
    """Current state; includes the result once the anonymization is done."""
    return _session(_jobs(request).get(session_id))


@router.post("/sessions/{session_id}/anonymize/stream")
async def anonymize_stream(
    session_id: SessionId, request: Request
) -> StreamingResponse:
    """Run the anonymization: SSE frames ``status`` (progress), then ``done`` or ``error``.

    A finished session answers ``done`` at once; a session already running
    answers ``error`` ``SESSION_BUSY``.
    """
    jobs = _jobs(request)
    job = jobs.get(session_id)
    t0 = time.perf_counter()
    queue = jobs.subscribe(job)  # before the start: no change is lost in between
    try:
        jobs.start(session_id)
    except SessionBusyError as exc:
        jobs.unsubscribe(job, queue)
        return sse_response(_single(_error_frame(exc)))
    return sse_response(_anonymize_frames(request, jobs, job, queue, t0))


@router.post("/sessions/{session_id}/edits/stream")
async def edits_stream(
    session_id: SessionId, payload: EditRequest, request: Request
) -> StreamingResponse:
    """Manual corrections (redact an area, un-redact a box, search and redact a text).

    SSE frames ``status`` while the document is redacted and verified again,
    then ``done`` or ``error``. Accurate mode only.
    """
    jobs = _jobs(request)
    job = jobs.get(session_id)
    t0 = time.perf_counter()
    queue = jobs.subscribe(job)
    try:
        future = jobs.start_edit(
            session_id,
            [(a.page, a.rect) for a in payload.add],
            payload.remove,
            payload.restore,
            payload.search.strip(),
        )
    except SessionBusyError as exc:
        jobs.unsubscribe(job, queue)
        return sse_response(_single(_error_frame(exc)))
    except Exception:
        jobs.unsubscribe(job, queue)
        raise
    return sse_response(_edit_frames(request, jobs, job, queue, future, t0))


@router.post("/sessions/{session_id}/cancel", status_code=202)
async def cancel(session_id: SessionId, request: Request) -> dict[str, bool]:
    """Stop the anonymization at the next step; the session and its files are deleted."""
    _jobs(request).cancel(session_id)
    return {"cancelled": True}


@router.get("/sessions/{session_id}/preview")
def preview(session_id: SessionId, request: Request) -> StreamingResponse:
    """The released document inline: the redacted PDF (accurate) or the Markdown (fast)."""
    path, fmt = _released(_jobs(request).ready_job(session_id), None)
    return open_file_response(path, MEDIA_TYPES[fmt], DOWNLOAD_NAMES[fmt], "inline")


@router.get("/sessions/{session_id}/download")
def download(
    session_id: SessionId,
    request: Request,
    format: Annotated[
        Format | None, Query(description="Default: pdf (accurate), md (fast)")
    ] = None,
) -> StreamingResponse:
    """The released document as an attachment: pdf or docx (accurate), md (fast)."""
    path, fmt = _released(_jobs(request).ready_job(session_id), format)
    return open_file_response(path, MEDIA_TYPES[fmt], DOWNLOAD_NAMES[fmt])


@router.get("/sessions/{session_id}/review")
def review(session_id: SessionId, request: Request) -> ReviewManifest:
    """Side-by-side review: pages with their original and anonymized renderings, and the boxes.

    Rectangles drawn on a rendering are sent to ``/edits/stream`` in the same
    space as ``boxes[].rect``. Accurate mode only.
    """
    manifest = _jobs(request).review(session_id)
    base = f"{router.prefix}/sessions/{session_id}/pages"
    version = manifest["version"]
    pages = [
        ReviewPage(
            **p,
            original=f"{base}/original/{p['index']}",
            anonymized=f"{base}/anonymized/{p['index']}?v={version}",
        )
        for p in manifest["pages"]
    ]
    return ReviewManifest.model_validate(
        manifest | {"session_id": manifest["id"], "pages": pages}
    )


@router.get("/sessions/{session_id}/pages/{which}/{page}")
def page_image(
    session_id: SessionId,
    which: Literal["original", "anonymized"],
    page: Annotated[int, Path(ge=0, le=500)],
    request: Request,
    dpi: Annotated[int, Query(ge=50, le=200)] = 110,
) -> StreamingResponse:
    """PNG of one page; the anonymized one only in accurate mode."""
    path = _jobs(request).render_page(session_id, which, page, dpi)
    return open_file_response(path, "image/png", f"pagina_{page + 1}.png", "inline")


@router.delete("/sessions/{session_id}", status_code=204)
async def delete(session_id: SessionId, request: Request) -> Response:
    """Delete the session and its files now."""
    _jobs(request).delete(session_id)
    return Response(status_code=204)
