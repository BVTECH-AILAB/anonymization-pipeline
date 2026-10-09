"""Liveness, readiness and version endpoints (used by the Kubernetes probes)."""

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from anonymization_pipeline import __version__
from anonymization_pipeline.services.anonymization import AnonymizationServiceDep

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is up. No dependency is checked here, otherwise
    a dependency outage would make Kubernetes restart every pod in a loop."""
    return {"status": "ok"}


@router.get("/readyz", response_model=None)
async def readyz(service: AnonymizationServiceDep) -> dict[str, Any] | JSONResponse:
    """Readiness: the VLM backend answers and has the configured model (503 otherwise).

    The body also reports the state of the NER and docling components (used by the web UI).
    """
    status = await service.status()
    if status["status"] != "ready":
        return JSONResponse(status_code=503, content=status)
    return status


@router.get("/version")
async def version(request: Request) -> dict[str, str]:
    return {"name": request.app.state.settings.app_name, "version": __version__}
