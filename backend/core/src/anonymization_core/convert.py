"""Uploads to PDF: a PDF is checked and kept, images become one page per frame."""

import io
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image, ImageOps

from anonymization_core.errors import (
    PasswordProtectedError,
    TooManyPagesError,
    UnreadableDocumentError,
    UnsupportedFormatError,
)
from anonymization_core.pdf_pages import pdfium_locked

PDF_SUFFIXES = {".pdf"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
# a phone photo is about 12-50 megapixels; larger frames or pages are refused before decoding
MAX_FRAME_PIXELS = 120_000_000


@pdfium_locked
def upload_to_pdf(
    data: bytes, suffix: str, dest: Path, max_pages: int, render_dpi: int = 300
) -> int:
    """Store the upload as a PDF; images become one page per frame.

    Args:
        data: Uploaded bytes.
        suffix: Lower-case file extension.
        dest: Where to write the PDF.
        max_pages: Page limit.
        render_dpi: Resolution pages are rendered at (OCR): a page too large to
            render at it is refused.

    Returns:
        Number of pages.

    Raises:
        UnsupportedFormatError: Unknown extension.
        UnreadableDocumentError: The file cannot be parsed.
        PasswordProtectedError: Encrypted PDF.
        TooManyPagesError: More pages than allowed.
    """
    if suffix in PDF_SUFFIXES:
        try:
            doc = pdfium.PdfDocument(data)
        except pdfium.PdfiumError as exc:
            if getattr(exc, "err_code", None) == pdfium_c.FPDF_ERR_PASSWORD:
                raise PasswordProtectedError() from exc
            raise UnreadableDocumentError() from exc
        except Exception as exc:
            raise UnreadableDocumentError() from exc
        try:
            pages = len(doc)
            if pages == 0:
                raise UnreadableDocumentError()
            if pages > max_pages:
                raise TooManyPagesError()
            scale = (render_dpi / 72) ** 2
            try:
                too_large = any(
                    w * h * scale > MAX_FRAME_PIXELS
                    for w, h in (doc.get_page_size(i) for i in range(pages))
                )
            except Exception as exc:
                raise UnreadableDocumentError() from exc
            if too_large:
                raise UnreadableDocumentError()
        finally:
            doc.close()
        dest.write_bytes(data)
        return pages
    if suffix not in IMAGE_SUFFIXES:
        raise UnsupportedFormatError()
    try:
        img = Image.open(io.BytesIO(data))
        n_frames = getattr(img, "n_frames", 1)
    except Exception as exc:
        raise UnreadableDocumentError() from exc
    if n_frames > max_pages:
        raise TooManyPagesError()
    doc = pdfium.PdfDocument.new()
    try:
        try:
            for i in range(n_frames):
                img.seek(i)
                # size is read from the header: refuse before decoding (decompression bomb)
                if img.width * img.height > MAX_FRAME_PIXELS:
                    raise UnreadableDocumentError()
                # phone photos: apply the EXIF rotation before anything else
                frame = ImageOps.exif_transpose(img.copy()).convert("RGB")
                dpi = (frame.info.get("dpi") or (200, 200))[0] or 200
                buf = io.BytesIO()
                frame.save(buf, "JPEG", quality=92)
                width, height = frame.width * 72 / dpi, frame.height * 72 / dpi
                page = doc.new_page(width, height)
                image = pdfium.PdfImage.new(doc)
                image.load_jpeg(io.BytesIO(buf.getvalue()), inline=True)
                image.set_matrix(pdfium.PdfMatrix().scale(width, height))
                page.insert_obj(image)
                page.gen_content()
        except UnreadableDocumentError:
            raise
        except Exception as exc:
            raise UnreadableDocumentError() from exc
        doc.save(dest)
    finally:
        doc.close()
    return n_frames
