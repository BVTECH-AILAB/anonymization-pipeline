"""Liveness, readiness and version endpoints (used by the Kubernetes probes)."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from anonymization_pipeline import __version__

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is up. No dependency is checked here, otherwise
    a dependency outage would make Kubernetes restart every pod in a loop."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    """Readiness: the service can serve traffic.

    Not ready (503) while a configured model is not loaded: documents would be refused.
    """
    if not getattr(request.app.state, "models_ready", False):
        return JSONResponse(
            status_code=503, content={"status": "not_ready", "reason": "models"}
        )
    return JSONResponse(content={"status": "ready"})


@router.get("/version")
async def version(request: Request) -> dict[str, str]:
    return {"name": request.app.state.settings.app_name, "version": __version__}
