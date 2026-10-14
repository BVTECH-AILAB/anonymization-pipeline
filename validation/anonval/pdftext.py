"""Text layer of a PDF, page by page (PDFium, as the service reads native pages), and visual
similarity of the censored layout PDF to the original."""

import base64
import binascii

import pypdfium2 as pdfium
from PIL import Image, ImageChops, ImageStat

SIMILARITY_DPI = 50


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


def _grey_pages(pdf: bytes) -> list[Image.Image]:
    doc = pdfium.PdfDocument(pdf)
    try:
        pages = []
        for page in doc:
            pages.append(page.render(scale=SIMILARITY_DPI / 72).to_pil().convert("L"))
            page.close()
        return pages
    finally:
        doc.close()


def similarity(original: bytes, censored: bytes) -> list[float]:
    """For every page of both PDFs: 1 - mean absolute difference of the grey levels (1 = the same
    image). The tags change a little of every page: the score compares renderers, it never
    reaches 1."""
    out = []
    for a, b in zip(_grey_pages(original), _grey_pages(censored), strict=False):
        if b.size != a.size:
            b = b.resize(a.size)
        mean = ImageStat.Stat(ImageChops.difference(a, b)).mean[0]
        out.append(round(1 - mean / 255, 4))
    return out


def layout_pages(result: dict, original: bytes | None = None) -> dict:
    """Result of the service with the censored layout PDF (``layout_pdf_b64``) replaced by its text
    (``layout_pages``, one string per page) and, given the ``original`` PDF, the similarity of
    every page to it (``layout_similarity``): the scoring needs nothing else, and the PDF would
    make every saved result much larger. An unreadable PDF leaves ``layout_error``."""
    out = {k: v for k, v in result.items() if k != "layout_pdf_b64"}
    encoded = result.get("layout_pdf_b64")
    if encoded:
        try:
            pdf = base64.b64decode(encoded, validate=True)
            out["layout_pages"] = page_texts(pdf)
        except (binascii.Error, ValueError, pdfium.PdfiumError):
            out["layout_error"] = "PDF con layout non leggibile"
            return out
        if original is not None and original.startswith(b"%PDF"):
            try:
                out["layout_similarity"] = similarity(original, pdf)
            except pdfium.PdfiumError:
                pass  # the original is not a readable PDF: no similarity
    return out
