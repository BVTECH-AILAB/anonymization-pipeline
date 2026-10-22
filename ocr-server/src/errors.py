"""Errors of the OCR server: an HTTP status and the generic message of the contract.

The body is ``{"error": "<message>"}`` (``OCR_CONTRACT.md`` of the backend);
never the text read or the exception of the engine.
"""


class OcrServerError(Exception):
    """Base class: a status and a generic message."""

    status_code: int = 500
    message: str = "OCR failed"


class ModelsNotLoadedError(OcrServerError):
    status_code = 503
    message = "models not loaded"


class ImageTooLargeError(OcrServerError):
    status_code = 400
    message = "image too large"


class UnreadableImageError(OcrServerError):
    status_code = 400
    message = "unreadable image"


class OcrFailedError(OcrServerError):
    status_code = 500
    message = "OCR failed"
