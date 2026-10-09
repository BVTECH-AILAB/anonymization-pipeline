"""Asynchronous jobs (used by the web UI)."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, Path, Query, UploadFile
from fastapi.responses import JSONResponse, Response

from anonymization_pipeline.api.anonymization import (
    Categories,
    Detectors,
    Extraction,
    Key,
    KeyHeader,
    Mode,
    Model,
    ObsoleteModel,
    Verification,
    phase_models,
)
from anonymization_pipeline.api.uploads import read_upload
from anonymization_pipeline.errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    ServerBusyError,
    UnredactableContentError,
)
from anonymization_pipeline.services.anonymization import AnonymizationServiceDep
from anonymization_pipeline.services.jobs import (
    Job,
    JobRegistry,
    JobRegistryDep,
    QueueFull,
)
from anonymization_pipeline.services.results import (
    content_disposition,
    result_payload,
    zip_name,
    zip_result,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])

JobId = Annotated[str, Path(max_length=64)]


def _job(jobs: JobRegistry, job_id: str) -> Job:
    job = jobs.get(job_id)
    if job is None:
        raise NotFoundError("Lavoro inesistente o scaduto.")
    return job


@router.post("", status_code=202, summary="Queue a document (asynchronous processing)")
async def create_job(
    service: AnonymizationServiceDep,
    jobs: JobRegistryDep,
    file: Annotated[UploadFile, File()],
    mode: Mode = None,
    verification: Verification = None,
    extraction: Extraction = None,
    model: ObsoleteModel = None,
    model_extraction: Model = None,
    model_detection: Model = None,
    model_redaction: Model = None,
    model_verification: Model = None,
    enable: Categories = None,
    disable: Categories = None,
    detectors: Detectors = None,
    transcript: Annotated[
        bool | None, Form(description="also the transcript IN CLEAR TEXT (debugging)")
    ] = None,
    key: Key = None,
    x_anon_key: KeyHeader = None,
) -> dict[str, Any]:
    models = phase_models(
        model, model_extraction, model_detection, model_redaction, model_verification
    )
    data = await read_upload(file, service.settings)
    cfg = await service.request_config(
        mode, verification, extraction, models, enable, disable, transcript, detectors
    )
    secret = service.key(key, x_anon_key)
    if cfg.phases.redaction.mode == "pseudonym" and not secret:
        raise BadRequestError(
            "Modalità pseudonimo: serve una chiave (campo key o header X-Anon-Key)."
        )
    try:
        job = jobs.add(file.filename or "documento", data, cfg, secret)
    except QueueFull as exc:
        raise ServerBusyError() from exc
    return job.as_dict(jobs.position(job))


@router.get("", summary="Queued, running and finished jobs (not expired)")
async def list_jobs(jobs: JobRegistryDep) -> list[dict[str, Any]]:
    return [job.as_dict(jobs.position(job)) for job in jobs.list()]


@router.get("/{job_id}", summary="Status and progress of a job")
async def get_job(job_id: JobId, jobs: JobRegistryDep) -> dict[str, Any]:
    job = _job(jobs, job_id)
    return job.as_dict(jobs.position(job))


@router.get(
    "/{job_id}/result",
    summary="Result of a completed job (JSON or ZIP)",
    response_model=None,
)
async def get_result(
    job_id: JobId,
    jobs: JobRegistryDep,
    output: Annotated[Literal["zip", "json"], Query(alias="format")] = "json",
) -> Response:
    job = _job(jobs, job_id)
    if job.status != "completed" or job.result is None:
        if job.status == "rejected":
            raise UnredactableContentError(extra=job.as_dict())
        raise ConflictError(
            f"Lavoro non completato (stato: {job.status}).", extra=job.as_dict()
        )
    if output == "json":
        return JSONResponse(result_payload(job.result))
    return Response(
        zip_result(job.result),
        media_type="application/zip",
        headers={"Content-Disposition": content_disposition(zip_name(job.result))},
    )


@router.delete("/{job_id}", status_code=204, summary="Delete a job and its result")
async def delete_job(job_id: JobId, jobs: JobRegistryDep) -> Response:
    if not jobs.delete(job_id):
        raise NotFoundError("Lavoro inesistente o scaduto.")
    return Response(status_code=204)
