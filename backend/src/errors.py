"""Service errors: an HTTP status, a stable code and a generic Italian message each.

User-facing messages are generic: never a stack trace, an SQL error or the
input the user sent. The engine has its own errors
(``src.services.anonymizer.errors``): ``service_error`` maps them here, and
``src.router.errors`` turns both into responses.
"""

from src.services.anonymizer import errors as engine


class ServiceError(Exception):
    """Base class for errors with a known HTTP mapping."""

    status_code: int = 500
    code: str = "INTERNAL_ERROR"
    user_message: str = "Si è verificato un errore interno. Riprova più tardi."


class NotFoundError(ServiceError):
    status_code = 404
    code = "NOT_FOUND"
    user_message = "Risorsa non trovata."


class ServiceUnavailableError(ServiceError):
    status_code = 503
    code = "SERVICE_UNAVAILABLE"
    user_message = "Servizio temporaneamente non disponibile. Riprova più tardi."


class NerUnavailableError(ServiceUnavailableError):
    code = "NER_UNAVAILABLE"


class UnsupportedFormatError(ServiceError):
    status_code = 415
    code = "UNSUPPORTED_FORMAT"
    user_message = "Formato non supportato. Carica un PDF (anche scansionato) o un'immagine JPG, PNG o TIFF."


class FileTooLargeError(ServiceError):
    status_code = 413
    code = "FILE_TOO_LARGE"
    user_message = "Il file supera la dimensione massima consentita."


class TooManyPagesError(ServiceError):
    status_code = 413
    code = "TOO_MANY_PAGES"
    user_message = "Il documento supera il numero massimo di pagine consentito."


class UnreadableDocumentError(ServiceError):
    status_code = 422
    code = "UNREADABLE_DOCUMENT"
    user_message = (
        "Impossibile leggere il documento: il file non è valido o è danneggiato."
    )


class PasswordProtectedError(ServiceError):
    status_code = 422
    code = "PASSWORD_PROTECTED"
    user_message = (
        "Il documento è protetto da password: rimuovi la password e caricalo di nuovo."
    )


class TooManyJobsError(ServiceError):
    status_code = 429
    code = "TOO_MANY_JOBS"
    user_message = "Troppi documenti in elaborazione. Riprova tra qualche minuto."


class JobNotFoundError(ServiceError):
    status_code = 404
    code = "JOB_NOT_FOUND"
    user_message = "Documento non trovato o scaduto."


class JobNotReadyError(ServiceError):
    status_code = 409
    code = "JOB_NOT_READY"
    user_message = "Il documento non è ancora pronto."


class ModeNotSupportedError(ServiceError):
    status_code = 409
    code = "MODE_NOT_SUPPORTED"
    user_message = "Operazione non disponibile per la modalità scelta."


class VerificationFailedError(ServiceError):
    status_code = 409
    code = "VERIFICATION_FAILED"
    user_message = (
        "Verifica non superata: il documento anonimizzato non viene rilasciato."
    )


_ENGINE_ERRORS: dict[type[engine.AnonymizationError], type[ServiceError]] = {
    engine.NerUnavailableError: NerUnavailableError,
    engine.UnsupportedFormatError: UnsupportedFormatError,
    engine.UnreadableDocumentError: UnreadableDocumentError,
    engine.PasswordProtectedError: PasswordProtectedError,
    engine.TooManyPagesError: TooManyPagesError,
    engine.PageNotFoundError: JobNotFoundError,
}


def service_error(exc: engine.AnonymizationError) -> ServiceError:
    """The HTTP error of an engine error; an unmapped one is a generic 500."""
    for engine_error, error in _ENGINE_ERRORS.items():
        if isinstance(exc, engine_error):
            return error()
    return ServiceError()
