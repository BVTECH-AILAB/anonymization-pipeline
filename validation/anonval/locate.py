"""Where the ground truth values are on the pages of the original PDF.

The characters of every page are read from the PDFium text page with their boxes, joined into a
text, and every value is searched with the tolerant ``text.find`` (spacing, case, values written
one character per box). Each occurrence keeps the boxes of its letters and digits, in the display
space of the service (top-left origin, points): the scoring compares them with the redaction
boxes the service returns. Independent of the service's text layer and detectors.
"""

from dataclasses import dataclass

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from anonval.text import find
from anonymization_pipeline.pdf import PDFIUM_LOCK
from anonymization_pipeline.pdf.geometry import Geometry, Rect


@dataclass(frozen=True)
class PageText:
    """Text of a page and the display box of each of its characters (None for line breaks)."""

    text: str
    boxes: tuple[Rect | None, ...]


@dataclass(frozen=True)
class Occurrence:
    """One occurrence of a value: the boxes of its letters and digits."""

    page: int  # zero-based
    chars: tuple[Rect, ...]


def page_texts(pdf: bytes) -> list[PageText]:
    """Every page of the PDF as text with character boxes."""
    out: list[PageText] = []
    with PDFIUM_LOCK:
        doc = pdfium.PdfDocument(pdf)
        try:
            for i in range(len(doc)):
                page = doc[i]
                geo = Geometry(page)
                textpage = page.get_textpage()
                chars: list[str] = []
                boxes: list[Rect | None] = []
                box = pdfium_c.FS_RECTF()
                for j in range(pdfium_c.FPDFText_CountChars(textpage.raw)):
                    code = pdfium_c.FPDFText_GetUnicode(textpage.raw, j)
                    char = chr(code) if code else " "
                    chars.append(char)
                    if not char.isspace() and pdfium_c.FPDFText_GetLooseCharBox(
                        textpage.raw, j, box
                    ):
                        boxes.append(
                            geo.to_display(box.left, box.bottom, box.right, box.top)
                        )
                    else:
                        boxes.append(None)
                textpage.close()
                page.close()
                out.append(PageText("".join(chars), tuple(boxes)))
        finally:
            doc.close()
    return out


def locate(pages: list[PageText], value: str) -> list[Occurrence]:
    """Every occurrence of ``value`` on the pages."""
    out: list[Occurrence] = []
    for i, page in enumerate(pages):
        for start, end in find(page.text, value):
            chars = tuple(
                box
                for char, box in zip(
                    page.text[start:end], page.boxes[start:end], strict=True
                )
                if box is not None and char.isalnum()
            )
            if chars:
                out.append(Occurrence(i, chars))
    return out
