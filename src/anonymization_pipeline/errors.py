"""Domain errors and their HTTP mapping.

User-facing messages are in Italian and generic: never return stack traces,
SQL errors or the input the user sent.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class ServiceError(Exception):
    """Base class for errors with a known HTTP mapping.

    Args:
        detail: Italian message for the user, instead of the generic one. It must never contain
            document content or personal data.
        extra: Additional fields of the response body (counts, categories, pages: no values).
        headers: Additional response headers (``Retry-After``).
    """

    status_code: int = 500
    code: str = "INTERNAL_ERROR"
    user_message: str = "Si è verificato un errore interno. Riprova più tardi."

    def __init__(
        self,
        detail: str | None = None,
        extra: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(self.code)
        if detail:
            self.user_message = detail
        self.extra = extra or {}
        self.headers = headers or {}


class NotFoundError(ServiceError):
    status_code = 404
    code = "NOT_FOUND"
    user_message = "Risorsa non trovata."


class ServiceUnavailableError(ServiceError):
    status_code = 503
    code = "SERVICE_UNAVAILABLE"
    user_message = "Servizio temporaneamente non disponibile. Riprova più tardi."


class BadRequestError(ServiceError):
    status_code = 400
    code = "BAD_REQUEST"
    user_message = "Richiesta non valida."


class InvalidDocumentError(ServiceError):
    status_code = 400
    code = "INVALID_DOCUMENT"
    user_message = "Documento non valido o in un formato non supportato."


class RestoreError(ServiceError):
    status_code = 403
    code = "RESTORE_FAILED"
    user_message = "Ripristino non riuscito: chiave errata o mappa non valida."


class ConflictError(ServiceError):
    status_code = 409
    code = "CONFLICT"
    user_message = "Operazione non possibile nello stato attuale."


class LengthRequiredError(ServiceError):
    status_code = 411
    code = "LENGTH_REQUIRED"
    user_message = "Header Content-Length obbligatorio."


class PayloadTooLargeError(ServiceError):
    status_code = 413
    code = "PAYLOAD_TOO_LARGE"
    user_message = "File oltre la dimensione massima consentita."


class UnredactableContentError(ServiceError):
    """The document cannot be returned: data left in clear text (fail-closed)."""

    status_code = 422
    code = "UNREDACTABLE_CONTENT"
    user_message = "Il documento non può essere anonimizzato in modo sicuro."


class VLMUnavailableError(ServiceUnavailableError):
    code = "VLM_UNAVAILABLE"
    user_message = "Il modello di anonimizzazione non è disponibile. Riprova più tardi."


class ServerBusyError(ServiceUnavailableError):
    code = "SERVER_BUSY"
    user_message = "Servizio occupato: riprova più tardi."

    def __init__(self) -> None:
        super().__init__(headers={"Retry-After": "30"})


def error_body(error: ServiceError) -> dict[str, Any]:
    """Response body of an error: code, Italian message and the error's extra fields."""
    return {"code": error.code, "detail": error.user_message, **error.extra}


async def _service_error_handler(request: Request, exc: Exception) -> JSONResponse:
    error = exc if isinstance(exc, ServiceError) else ServiceError()
    logger.warning("%s on %s %s", error.code, request.method, request.url.path)
    return JSONResponse(
        status_code=error.status_code, content=error_body(error), headers=error.headers
    )


async def _validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # FastAPI's default 422 body echoes the rejected "input": it may contain
    # personal data, so only location, message and type are returned.
    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    safe = [
        {"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")}
        for e in errors
    ]
    logger.info(
        "Validation failed on %s %s: %d error(s)",
        request.method,
        request.url.path,
        len(safe),
    )
    return JSONResponse(
        status_code=422,
        content={
            "code": "VALIDATION_ERROR",
            "detail": "Dati non validi.",
            "errors": safe,
        },
    )


async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "Unhandled %s on %s %s",
        type(exc).__name__,
        request.method,
        request.url.path,
        exc_info=exc,
    )
    error = ServiceError()
    return JSONResponse(
        status_code=error.status_code,
        content={"code": error.code, "detail": error.user_message},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the error handlers to ``app``."""
    app.add_exception_handler(ServiceError, _service_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
