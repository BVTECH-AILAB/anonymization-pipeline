"""Anonymization API: upload, progress (Server-Sent Events), result, edits, downloads.

URLs carry only random job ids: never names, codici fiscali or file names.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Path, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from anonmyzation_pipeline.errors import VerificationFailedError
from anonmyzation_pipeline.services.anonymization.taxonomy import LABELS
from anonmyzation_pipeline.services.jobs import TERMINAL, JobManagerDep

router = APIRouter(prefix="/anonymizations", tags=["anonymization"])

JobId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
# Generic download names: the original file name may contain the patient's name.
DOWNLOAD_NAMES = {
    "pdf": "documento_anonimizzato.pdf",
    "docx": "documento_anonimizzato.docx",
}
MEDIA_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
SSE_KEEPALIVE_SECONDS = 15


class LabelInfo(BaseModel):
    it: str
    kind: Literal["text", "visual"]
    redact: bool


class JobCreated(BaseModel):
    id: str
    pages: int
    size: int


class JobStatus(BaseModel):
    id: str
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
    jobs: JobManagerDep, file: Annotated[UploadFile, File()]
) -> JobCreated:
    """Upload a document (PDF, scanned PDF, JPG, PNG, TIFF) and start the anonymization."""
    # never more than the limit in memory: one byte over is enough to refuse it
    data = await file.read(jobs.settings.max_upload_mb * 1024 * 1024 + 1)
    job = await asyncio.to_thread(jobs.create, data, file.filename or "documento")
    return JobCreated(id=job.id, pages=job.pages, size=job.size)


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


@router.get("/{job_id}/pages/{which}/{page}")
def page_image(
    job_id: JobId,
    which: Literal["original", "anonymized"],
    page: Annotated[int, Path(ge=0, le=500)],
    jobs: JobManagerDep,
    dpi: Annotated[int, Query(ge=50, le=200)] = 110,
) -> FileResponse:
    """PNG preview of one page."""
    path = jobs.render_page(job_id, which, page, dpi)
    return FileResponse(
        path, media_type="image/png", headers={"Cache-Control": "no-store"}
    )


@router.get("/{job_id}/download/{fmt}")
def download(
    job_id: JobId, fmt: Literal["pdf", "docx"], jobs: JobManagerDep
) -> FileResponse:
    """The anonymized document. Refused when verification failed (fail-closed)."""
    job = jobs.ready_job(job_id)
    if not job.verified:
        raise VerificationFailedError()
    path = job.anonymized if fmt == "pdf" else job.docx
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
