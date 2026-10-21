"""Errors of the anonymization engine, free of any transport.

Callers map them to their own responses: the service turns them into the
Italian HTTP errors of ``anonmyzation_pipeline.errors`` (``service_error``).
Messages never carry document text.
"""


class AnonymizationError(Exception):
    """Base class of the engine errors."""


class NerUnavailableError(AnonymizationError):
    """The NER service is not configured, is down, or answered with another shape."""


class UnsupportedFormatError(AnonymizationError):
    """The upload is neither a PDF nor a supported image."""


class UnreadableDocumentError(AnonymizationError):
    """The file cannot be parsed, has no pages, or a page is too large to render."""


class PasswordProtectedError(AnonymizationError):
    """The PDF is encrypted."""


class TooManyPagesError(AnonymizationError):
    """More pages or image frames than allowed."""


class PageNotFoundError(AnonymizationError):
    """The page index is outside the document."""
