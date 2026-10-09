"""Liveness, readiness and version endpoints (used by the Kubernetes probes)."""

from fastapi import APIRouter, Request

from anonymization_pipeline import __version__

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is up. No dependency is checked here, otherwise
    a dependency outage would make Kubernetes restart every pod in a loop."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz() -> dict[str, str]:
    """Readiness: the service can serve traffic."""
    return {"status": "ready"}


@router.get("/version")
async def version(request: Request) -> dict[str, str]:
    return {"name": request.app.state.settings.app_name, "version": __version__}
