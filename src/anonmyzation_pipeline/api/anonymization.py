"""Anonymization API: upload, progress (Server-Sent Events), result, preview, edits, downloads.

``mode=accurate`` (default) releases the redacted PDF and Word copy and supports the
side-by-side review (preview, edits); ``mode=fast`` releases only the anonymized
text as Markdown. URLs carry only random job ids: never names, codici fiscali or
file names.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from anonymization_core.taxonomy import LABELS
from fastapi import APIRouter, File, Path, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from anonmyzation_pipeline.errors import (
    ModeNotSupportedError,
    VerificationFailedError,
)
from anonmyzation_pipeline.services.jobs import TERMINAL, JobManagerDep, Mode

router = APIRouter(prefix="/anonymizations", tags=["anonymization"])

JobId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
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
# the downloads each mode produces
FORMATS: dict[str, set[str]] = {"accurate": {"pdf", "docx"}, "fast": {"md"}}
SSE_KEEPALIVE_SECONDS = 15


class LabelInfo(BaseModel):
    it: str
    kind: Literal["text", "visual"]
    redact: bool


class JobCreated(BaseModel):
    id: str
    pages: int
    size: int
    mode: Mode


class JobStatus(BaseModel):
    id: str
    mode: Mode
    status: str
    step: str
    percent: int
    error_code: str | None = None
    result: dict[str, Any] | None = None


Coordinate = Annotated[float, Field(ge=-100, le=20000, allow_inf_nan=False)]


class AreaEdit(BaseModel):
    page: int = Field(ge=0, le=500)
    rect: tuple[Coordinate, Coordinate, Coordinate, Coordinate]


class EditRequest(BaseModel):
    add: list[AreaEdit] = Field(default_factory=list, max_length=500)
    remove: list[int] = Field(default_factory=list, max_length=5000)
    restore: list[int] = Field(default_factory=list, max_length=5000)
    search: str = Field(default="", max_length=200)


class PreviewPage(BaseModel):
    """One page: size in PDF points (display space) and the URLs of its two renderings."""

    index: int
    width: float
    height: float
    original: str
    anonymized: str


class PreviewBox(BaseModel):
    """A detected or manual area; ``rect`` is ``(x0, y0, x1, y1)`` in points, top-left origin."""

    id: int
    page: int
    rect: tuple[float, float, float, float]
    label: str
    layer: str
    redact: bool


class PreviewManifest(BaseModel):
    """Everything the side-by-side review needs; ``version`` changes with every edit."""

    id: str
    mode: Mode
    version: int
    verified: bool
    pages: list[PreviewPage]
    boxes: list[PreviewBox]
    labels: dict[str, str]


@router.get("/labels")
async def labels() -> dict[str, LabelInfo]:
    """PII taxonomy: Italian name, kind and default policy of every label."""
    return {
        k: LabelInfo(
            it=v.it, kind="visual" if v.kind == "visual" else "text", redact=v.redact
        )
        for k, v in LABELS.items()
    }


@router.post("", status_code=202)
async def create(
    jobs: JobManagerDep,
    file: Annotated[UploadFile, File()],
    mode: Annotated[Mode, Query()] = "accurate",
) -> JobCreated:
    """Upload a document (PDF, scanned PDF, JPG, PNG, TIFF) and start the anonymization.

    ``accurate``: redacted PDF, reviewable and editable. ``fast``: anonymized text as Markdown.
    """
    # never more than the limit in memory: one byte over is enough to refuse it
    data = await file.read(jobs.settings.max_upload_mb * 1024 * 1024 + 1)
    job = await asyncio.to_thread(jobs.create, data, file.filename or "documento", mode)
    return JobCreated(id=job.id, pages=job.pages, size=job.size, mode=job.mode)


@router.get("/{job_id}")
async def status(job_id: JobId, jobs: JobManagerDep) -> JobStatus:
    """Current state; includes the result once the job is done."""
    return JobStatus(**jobs.get(job_id).snapshot())


@router.get("/{job_id}/events")
async def events(
    job_id: JobId, request: Request, jobs: JobManagerDep
) -> StreamingResponse:
    """Server-Sent Events: one message per progress change, then the final state."""
    job = jobs.get(job_id)
    queue = jobs.subscribe(job)  # before the snapshot: no change is lost in between

    async def stream() -> AsyncIterator[str]:
        try:
            state = job.snapshot()
            yield f"data: {json.dumps(state)}\n\n"
            while state["status"] not in TERMINAL:
                if await request.is_disconnected():
                    return
                try:
                    state = await asyncio.wait_for(
                        queue.get(), timeout=SSE_KEEPALIVE_SECONDS
                    )
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {json.dumps(state)}\n\n"
        finally:
            jobs.unsubscribe(job, queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{job_id}/cancel", status_code=202)
async def cancel(job_id: JobId, jobs: JobManagerDep) -> dict[str, bool]:
    """Stop the job at the next step; its files are deleted."""
    jobs.cancel(job_id)
    return {"cancelled": True}


@router.post("/{job_id}/edits")
async def edit(
    job_id: JobId, payload: EditRequest, jobs: JobManagerDep
) -> dict[str, Any]:
    """Manual corrections: redact an area, un-redact a box, search and redact a text."""
    return await asyncio.to_thread(
        jobs.edit,
        job_id,
        [(a.page, a.rect) for a in payload.add],
        payload.remove,
        payload.restore,
        payload.search.strip(),
    )


@router.get("/{job_id}/preview")
def preview(job_id: JobId, jobs: JobManagerDep) -> PreviewManifest:
    """Side-by-side review: pages with their original and anonymized renderings, and the boxes.

    Rectangles drawn on a rendering are sent to ``/edits`` in the same space as
    ``boxes[].rect``. Accurate mode only.
    """
    manifest = jobs.preview(job_id)
    base = f"{router.prefix}/{job_id}/pages"
    version = manifest["version"]
    pages = [
        PreviewPage(
            **p,
            original=f"{base}/original/{p['index']}",
            anonymized=f"{base}/anonymized/{p['index']}?v={version}",
        )
        for p in manifest["pages"]
    ]
    return PreviewManifest.model_validate(manifest | {"pages": pages})


@router.get("/{job_id}/pages/{which}/{page}")
def page_image(
    job_id: JobId,
    which: Literal["original", "anonymized"],
    page: Annotated[int, Path(ge=0, le=500)],
    jobs: JobManagerDep,
    dpi: Annotated[int, Query(ge=50, le=200)] = 110,
) -> FileResponse:
    """PNG preview of one page; the anonymized one only in accurate mode."""
    path = jobs.render_page(job_id, which, page, dpi)
    return FileResponse(
        path, media_type="image/png", headers={"Cache-Control": "no-store"}
    )


@router.get("/{job_id}/download/{fmt}")
def download(
    job_id: JobId, fmt: Literal["pdf", "docx", "md"], jobs: JobManagerDep
) -> FileResponse:
    """The anonymized document: pdf or docx (accurate), md (fast).

    Refused when verification failed (fail-closed).
    """
    job = jobs.ready_job(job_id)
    if fmt not in FORMATS[job.mode]:
        raise ModeNotSupportedError()
    if not job.verified:
        raise VerificationFailedError()
    path = {"pdf": job.anonymized, "docx": job.docx, "md": job.markdown}[fmt]
    return FileResponse(
        path,
        media_type=MEDIA_TYPES[fmt],
        filename=DOWNLOAD_NAMES[fmt],
        headers={"Cache-Control": "no-store"},
    )


@router.delete("/{job_id}", status_code=204)
async def delete(job_id: JobId, jobs: JobManagerDep) -> Response:
    """Delete the job and its files now."""
    jobs.delete(job_id)
    return Response(status_code=204)
