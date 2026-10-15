"""Liveness, readiness and version endpoints (used by the Kubernetes probes)."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from anonmyzation_pipeline import __version__

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is up. No dependency is checked here, otherwise
    a slow model load would make Kubernetes restart every pod in a loop."""
    return {"status": "ok"}


@router.get("/readyz", response_model=None)
async def readyz(request: Request) -> dict[str, str] | JSONResponse:
    """Readiness: the service can serve traffic (OCR and NER models loaded)."""
    if not request.app.state.pipeline.ready:
        return JSONResponse(
            status_code=503, content={"status": "unavailable", "reason": "models"}
        )
    return {"status": "ready"}


@router.get("/version")
async def version(request: Request) -> dict[str, str]:
    return {"name": request.app.state.settings.app_name, "version": __version__}
