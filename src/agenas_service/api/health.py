"""Liveness, readiness and version endpoints (used by the Kubernetes probes)."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from agenas_service import __version__
from agenas_service.db.engine import ping

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is up. No dependency is checked here, otherwise
    a database outage would make Kubernetes restart every pod in a loop."""
    return {"status": "ok"}


@router.get("/readyz", response_model=None)
async def readyz(request: Request) -> dict[str, str] | JSONResponse:
    """Readiness: the service can serve traffic (database reachable)."""
    if not await ping(request.app.state.engine):
        return JSONResponse(
            status_code=503, content={"status": "unavailable", "reason": "database"}
        )
    return {"status": "ready"}


@router.get("/version")
async def version(request: Request) -> dict[str, str]:
    return {"name": request.app.state.settings.app_name, "version": __version__}
