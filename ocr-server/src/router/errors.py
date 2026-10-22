"""Exception handler: an OCR server error becomes ``{"error": "<generic message>"}``."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.errors import OcrServerError


async def _ocr_error_handler(_: Request, exc: Exception) -> JSONResponse:
    error = exc if isinstance(exc, OcrServerError) else OcrServerError()
    return JSONResponse({"error": error.message}, status_code=error.status_code)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the handler of the OCR server errors to ``app``."""
    app.add_exception_handler(OcrServerError, _ocr_error_handler)
