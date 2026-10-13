"""Text layer of a PDF, page by page (PDFium, as the service reads native pages)."""

import base64
import binascii

import pypdfium2 as pdfium


def page_texts(pdf: bytes) -> list[str]:
    """Text layer of every page, as PDFium extracts it."""
    doc = pdfium.PdfDocument(pdf)
    try:
        texts = []
        for page in doc:
            textpage = page.get_textpage()
            texts.append(textpage.get_text_range())
            textpage.close()
            page.close()
        return texts
    finally:
        doc.close()


def layout_pages(result: dict) -> dict:
    """Result of the service with the censored layout PDF (``layout_pdf_b64``) replaced by its text
    (``layout_pages``, one string per page): the scoring needs only the text, and the PDF would
    make every saved result much larger. An unreadable PDF leaves ``layout_error``."""
    out = {k: v for k, v in result.items() if k != "layout_pdf_b64"}
    encoded = result.get("layout_pdf_b64")
    if encoded:
        try:
            out["layout_pages"] = page_texts(base64.b64decode(encoded, validate=True))
        except (binascii.Error, ValueError, pdfium.PdfiumError):
            out["layout_error"] = "PDF con layout non leggibile"
    return out
