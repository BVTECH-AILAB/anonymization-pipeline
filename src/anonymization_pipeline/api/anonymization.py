"""Anonymization API: upload a document, follow the job, download the redacted PDF.

The job id is the only key to the document: it is never logged in full (see ``privacy``). The
download is refused unless the verification passed (fail-closed). Responses hold counters,
categories and pages, never the values found.
"""

import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, File, Form, Path, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from anonymization_pipeline.config import Settings
from anonymization_pipeline.errors import (
    BadRequestError,
    DocumentBlockedError,
    InvalidDocumentError,
    JobNotReadyError,
    NotFoundError,
    PayloadTooLargeError,
    TooManyJobsError,
)
from anonymization_pipeline.ingest import DocumentError, file_kind
from anonymization_pipeline.pipeline_config import PipelineConfig, request_config
from anonymization_pipeline.services.jobs import (
    TERMINAL,
    Job,
    JobRegistryDep,
    QueueFull,
)

router = APIRouter(prefix="/anonymizations", tags=["anonymization"])

JobId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{22}$")]
DOWNLOAD_NAME = "documento_anonimizzato.pdf"
KEEPALIVE_S = 15.0


class JobCreated(BaseModel):
    id: str
    status: str


class FailureOut(BaseModel):
    check: str
    page: int | None = Field(
        description="Pagina (da 1), se il controllo riguarda una pagina"
    )
    category: str | None


class ResultOut(BaseModel):
    pages: int
    kind: str
    scanned_pages: list[int] = Field(description="Pagine scansionate (da 1)")
    counts: dict[str, int] = Field(description="Valori distinti trovati per categoria")
    boxes: int
    redacted_pages: list[int] = Field(description="Pagine redatte (da 1)")
    failures: list[FailureOut]
    timings: dict[str, float]


class JobOut(BaseModel):
    id: str
    status: str
    phase: str | None
    reason: str | None = Field(description="Perché il documento è bloccato")
    error: str | None
    created: float
    started: float | None
    finished: float | None
    result: ResultOut | None


def _out(job: Job) -> JobOut:
    r = job.result
    result = None
    if r is not None:
        result = ResultOut(
            pages=r.pages,
            kind=r.kind,
            scanned_pages=[p + 1 for p in r.scanned_pages],
            counts=r.counts,
            boxes=r.boxes,
            redacted_pages=[p + 1 for p in sorted(r.strategies)],
            failures=[
                FailureOut(
                    check=f.check,
                    page=None if f.page is None else f.page + 1,
                    category=f.category,
                )
                for f in r.failures
            ],
            timings=r.timings,
        )
    return JobOut(
        id=job.id,
        status=job.status,
        phase=job.phase,
        reason=r.reason if r else None,
        error=job.error,
        created=job.created,
        started=job.started,
        finished=job.finished,
        result=result,
    )


def _job(jobs: JobRegistryDep, job_id: str) -> Job:
    job = jobs.get(job_id)
    if job is None:
        raise NotFoundError()
    return job


async def _read(upload: UploadFile, settings: Settings) -> bytes:
    limit = int(settings.max_upload_mb * 1024 * 1024)
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise PayloadTooLargeError(
            f"File oltre il limite di {settings.max_upload_mb:g} MB."
        )
    if not data:
        raise InvalidDocumentError("File vuoto.")
    return data


@router.post("", status_code=202)
async def create(
    request: Request,
    jobs: JobRegistryDep,
    file: Annotated[UploadFile, File()],
    enable: Annotated[str, Form(max_length=2000)] = "",
    disable: Annotated[str, Form(max_length=2000)] = "",
) -> JobCreated:
    """Queue a document (PDF or image) for anonymization.

    ``enable`` and ``disable`` are groups or categories separated by commas; the protected
    ones (direct identifiers) cannot be disabled.
    """
    service: PipelineConfig = request.app.state.pipeline_config
    try:
        cfg = request_config(service, (enable,), (disable,))
    except ValueError as exc:  # Italian message naming groups or categories only
        raise BadRequestError(str(exc)) from exc
    data = await _read(file, request.app.state.settings)
    try:
        file_kind(data)
    except DocumentError as exc:
        raise InvalidDocumentError(str(exc)) from exc
    try:
        job = jobs.add(data, cfg)
    except QueueFull as exc:
        raise TooManyJobsError() from exc
    return JobCreated(id=job.id, status=job.status)


@router.get("/{job_id}")
async def status(job_id: JobId, jobs: JobRegistryDep) -> JobOut:
    """Status of a job and, once finished, its outcome."""
    return _out(_job(jobs, job_id))


@router.get("/{job_id}/events")
async def events(job_id: JobId, jobs: JobRegistryDep) -> StreamingResponse:
    """Server-sent events: the job status at every change, until it is finished."""
    job = _job(jobs, job_id)

    async def stream() -> AsyncIterator[str]:
        while True:
            version = job.version
            yield f"data: {json.dumps(_out(job).model_dump())}\n\n"
            if job.status in TERMINAL:
                return
            while not await jobs.wait(job, version, KEEPALIVE_S):
                yield ": keep-alive\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/{job_id}/download")
async def download(job_id: JobId, jobs: JobRegistryDep) -> Response:
    """The redacted PDF, only when the verification passed."""
    job = _job(jobs, job_id)
    if job.status == "blocked":
        raise DocumentBlockedError()
    if job.status != "done" or job.result is None or job.result.pdf is None:
        raise JobNotReadyError()
    return Response(
        job.result.pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{DOWNLOAD_NAME}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/{job_id}/cancel", status_code=202)
async def cancel(job_id: JobId, jobs: JobRegistryDep) -> dict[str, bool]:
    """Stop a waiting or running job; a finished job is left as it is."""
    _job(jobs, job_id)
    return {"cancelled": jobs.cancel(job_id)}


@router.delete("/{job_id}", status_code=204)
async def delete(job_id: JobId, jobs: JobRegistryDep) -> Response:
    """Delete the job and its document now (also when already expired)."""
    jobs.delete(job_id)
    return Response(status_code=204)
