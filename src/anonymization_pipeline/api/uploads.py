"""Reading of uploaded files within the configured size limit."""

from fastapi import UploadFile

from anonymization_pipeline.config import Settings
from anonymization_pipeline.errors import (
    BadRequestError,
    InvalidDocumentError,
    PayloadTooLargeError,
)


async def read_upload(upload: UploadFile, settings: Settings) -> bytes:
    """Content of an uploaded file.

    Raises:
        PayloadTooLargeError: Over ``ANONPIP_MAX_UPLOAD_MB``.
        InvalidDocumentError: Empty file.
    """
    limit = int(settings.max_upload_mb * 1024 * 1024)
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise PayloadTooLargeError(
            f"File oltre il limite di {settings.max_upload_mb:g} MB."
        )
    if not data:
        raise InvalidDocumentError("File vuoto.")
    return data


async def read_markdown(upload: UploadFile, settings: Settings) -> str:
    """Uploaded Markdown, which must be UTF-8.

    Raises:
        BadRequestError: Not UTF-8.
    """
    try:
        return (await read_upload(upload, settings)).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BadRequestError("Il Markdown deve essere in UTF-8.") from exc
