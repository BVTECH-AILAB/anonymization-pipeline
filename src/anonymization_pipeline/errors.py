"""Domain errors and their HTTP mapping.

User-facing messages are in Italian and generic: never return stack traces,
SQL errors or the input the user sent.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class ServiceError(Exception):
    """Base class for errors with a known HTTP mapping.

    A subclass may be raised with a more specific Italian message, which must never contain
    document content or user input.
    """

    status_code: int = 500
    code: str = "INTERNAL_ERROR"
    user_message: str = "Si è verificato un errore interno. Riprova più tardi."
    headers: dict[str, str] | None = None

    def __init__(self, message: str | None = None) -> None:
        if message:
            self.user_message = message
        super().__init__(self.user_message)


class BadRequestError(ServiceError):
    status_code = 400
    code = "BAD_REQUEST"
    user_message = "Richiesta non valida."


class NotFoundError(ServiceError):
    status_code = 404
    code = "NOT_FOUND"
    user_message = "Risorsa non trovata."


class JobNotReadyError(ServiceError):
    status_code = 409
    code = "JOB_NOT_READY"
    user_message = "Il documento non è ancora pronto."


class DocumentBlockedError(ServiceError):
    status_code = 409
    code = "DOCUMENT_BLOCKED"
    user_message = (
        "Il documento non può essere rilasciato: la verifica dell'anonimizzazione "
        "non è stata superata."
    )


class LengthRequiredError(ServiceError):
    status_code = 411
    code = "LENGTH_REQUIRED"
    user_message = "Dimensione della richiesta non indicata."


class PayloadTooLargeError(ServiceError):
    status_code = 413
    code = "PAYLOAD_TOO_LARGE"
    user_message = "File troppo grande."


class InvalidDocumentError(ServiceError):
    status_code = 422
    code = "INVALID_DOCUMENT"
    user_message = "Documento non valido."


class TooManyJobsError(ServiceError):
    status_code = 429
    code = "TOO_MANY_JOBS"
    user_message = "Troppi documenti in coda. Riprova tra qualche minuto."
    headers = {"Retry-After": "30"}


class ServiceUnavailableError(ServiceError):
    status_code = 503
    code = "SERVICE_UNAVAILABLE"
    user_message = "Servizio temporaneamente non disponibile. Riprova più tardi."


def error_body(error: ServiceError) -> dict[str, str]:
    """JSON body of an error response."""
    return {"code": error.code, "detail": error.user_message}


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
