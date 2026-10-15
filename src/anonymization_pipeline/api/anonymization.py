"""Anonymization API: upload a document, follow the job, download the redacted PDF.

The job id is the only key to the document: it is never logged in full (see ``privacy``). The
download is refused unless the verification passed (fail-closed). Responses hold counters,
categories and pages, never the values found.
"""

import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, File, Form, Header, Path, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from anonymization_pipeline.api.uploads import read_upload
from anonymization_pipeline.config import Settings
from anonymization_pipeline.errors import (
    BadRequestError,
    DocumentBlockedError,
    InvalidDocumentError,
    JobNotReadyError,
    NotFoundError,
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
MARKDOWN_NAME = "documento_anonimizzato.md"
MAP_NAME = "documento_anonimizzato.mappa.enc"
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


class BoxOut(BaseModel):
    page: int = Field(description="Pagina (da 1)")
    rect: tuple[float, float, float, float] = Field(
        description="x0, y0, x1, y1 in punti, origine in alto a sinistra della pagina mostrata"
    )
    category: str


class ResultOut(BaseModel):
    pages: int
    markdown: bool = Field(description="Il Markdown anonimizzato è disponibile")
    mode: str | None = Field(description="Sostituzione nel Markdown")
    replacements: int = Field(description="Sostituzioni nel Markdown")
    map: bool = Field(
        description="La mappa cifrata è disponibile (modalità pseudonimo)"
    )
    kind: str
    scanned_pages: list[int] = Field(description="Pagine scansionate (da 1)")
    counts: dict[str, int] = Field(description="Valori distinti trovati per categoria")
    boxes: list[BoxOut] = Field(description="Riquadri applicati, senza i valori")
    redacted_pages: list[int] = Field(description="Pagine redatte (da 1)")
    strategies: dict[int, str] = Field(
        description="Pagina (da 1) -> surgical | flatten | scan: come è stata redatta"
    )
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
            markdown=r.markdown is not None,
            mode=r.mode,
            replacements=r.replacements,
            map=r.encrypted_map is not None,
            kind=r.kind,
            scanned_pages=[p + 1 for p in r.scanned_pages],
            counts=r.counts,
            boxes=[
                BoxOut(page=b.page + 1, rect=b.rect, category=b.category)
                for b in r.boxes
            ],
            redacted_pages=[p + 1 for p in sorted(r.strategies)],
            strategies={p + 1: s for p, s in sorted(r.strategies.items())},
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


@router.post("", status_code=202)
async def create(
    request: Request,
    jobs: JobRegistryDep,
    file: Annotated[UploadFile, File()],
    enable: Annotated[str, Form(max_length=2000)] = "",
    disable: Annotated[str, Form(max_length=2000)] = "",
    mode: Annotated[str, Form(max_length=20)] = "",
    key: Annotated[str, Form(max_length=500)] = "",
    x_anon_key: Annotated[str, Header(max_length=500)] = "",
) -> JobCreated:
    """Queue a document (PDF or image) for anonymization.

    ``enable`` and ``disable`` are groups or categories separated by commas; the protected
    ones (direct identifiers) cannot be disabled. ``mode`` is the replacement of the Markdown
    (``tag``, ``pseudonym``, ``redaction``); ``pseudonym`` needs the key of the map, in ``key``
    or in the ``X-Anon-Key`` header, or the service key (``ANONPIP_VAULT_KEY``).
    """
    service: PipelineConfig = request.app.state.pipeline_config
    settings: Settings = request.app.state.settings
    try:
        cfg = request_config(service, (enable,), (disable,), mode or None)
    except ValueError as exc:  # Italian message naming groups, categories or modes only
        raise BadRequestError(str(exc)) from exc
    secret = key or x_anon_key or settings.vault_key
    if cfg.markdown.enabled and cfg.redaction.mode == "pseudonym" and not secret:
        raise BadRequestError(
            "Modalità pseudonimo: serve una chiave per la mappa, oppure usare la modalità tag"
        )
    data = await read_upload(file, settings)
    try:
        file_kind(data)
    except DocumentError as exc:
        raise InvalidDocumentError(str(exc)) from exc
    try:
        job = jobs.add(data, cfg, secret or None)
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


def _released(jobs: JobRegistryDep, job_id: str) -> Job:
    job = _job(jobs, job_id)
    if job.status == "blocked":
        raise DocumentBlockedError()
    if job.status != "done" or job.result is None:
        raise JobNotReadyError()
    return job


@router.get("/{job_id}/markdown")
async def markdown(job_id: JobId, jobs: JobRegistryDep) -> Response:
    """The anonymized Markdown, only when the verification passed."""
    job = _released(jobs, job_id)
    if job.result is None or job.result.markdown is None:
        raise NotFoundError()
    return Response(
        job.result.markdown,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{MARKDOWN_NAME}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/{job_id}/map")
async def encrypted_map(job_id: JobId, jobs: JobRegistryDep) -> Response:
    """The encrypted map of the pseudonym mode (it opens only with its key)."""
    job = _released(jobs, job_id)
    if job.result is None or job.result.encrypted_map is None:
        raise NotFoundError()
    return Response(
        job.result.encrypted_map,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{MAP_NAME}"',
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
