"""Service errors: an HTTP status, a stable code and a generic Italian message each.

User-facing messages are generic: never a stack trace, an SQL error or the
input the user sent. The engine has its own errors
(``src.services.anonymizer.errors``): ``service_error`` maps them here, and
``src.router.errors`` turns both into responses.
"""

from src.services.anonymizer import errors as engine


class ServiceError(Exception):
    """Base class for errors with a known HTTP mapping.

    ``user_title`` heads the ``error`` frame of an SSE stream, where the error
    arrives after the response started and has no HTTP status of its own.
    """

    status_code: int = 500
    code: str = "INTERNAL_ERROR"
    user_title: str = "Errore temporaneo"
    user_message: str = "Si è verificato un errore interno. Riprova più tardi."


class NotFoundError(ServiceError):
    status_code = 404
    code = "NOT_FOUND"
    user_message = "Risorsa non trovata."


class ServiceUnavailableError(ServiceError):
    status_code = 503
    code = "SERVICE_UNAVAILABLE"
    user_title = "Servizio non disponibile"
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
    user_title = "Documento non trovato"
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
    user_title = "Verifica non superata"
    user_message = (
        "Verifica non superata: il documento anonimizzato non viene rilasciato."
    )


class SessionBusyError(ServiceError):
    """The session is already being anonymized or edited."""

    status_code = 409
    code = "SESSION_BUSY"
    user_title = "Elaborazione in corso"
    user_message = "Il documento è già in elaborazione: attendi che finisca."


class ProcessingFailedError(ServiceError):
    """The anonymization or an edit failed for an unexpected reason."""

    code = "PROCESSING_FAILED"
    user_title = "Elaborazione non riuscita"
    user_message = (
        "Non è stato possibile anonimizzare il documento. Riprova o caricalo di nuovo."
    )


class JobCancelledError(ServiceError):
    """The session was cancelled while it was being anonymized."""

    status_code = 409
    code = "CANCELLED"
    user_title = "Elaborazione annullata"
    user_message = "L'elaborazione è stata annullata e il documento eliminato."


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


def error_for_code(code: str | None) -> ServiceError:
    """The service error of a stable code (the ``error_code`` of a job); unknown: generic."""
    pending = list(ServiceError.__subclasses__())
    while pending:
        cls = pending.pop()
        if cls.code == code:
            return cls()
        pending += cls.__subclasses__()
    return ServiceError()
