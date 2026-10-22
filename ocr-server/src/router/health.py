"""Liveness and readiness: one probe, 503 until the models are loaded."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.errors import ModelsNotLoadedError
from src.models.ocr import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=None)
async def health(request: Request) -> HealthResponse | JSONResponse:
    """200 once every model is loaded (the backend's readiness waits for it)."""
    engine = getattr(request.app.state, "engine", None)
    if engine is None or not engine.ready:
        error = ModelsNotLoadedError()
        return JSONResponse({"error": error.message}, status_code=error.status_code)
    return HealthResponse(status="healthy")
