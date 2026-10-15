"""Native text layer of a PDF page, read from the PDFium text page.

Every character keeps its loose box (full ascent to descent, like a text selection), converted
to display space. Invisible text (render mode 3, white text, text outside the page) is kept: it
is in the file, so it must be found and redacted like visible text. The caller holds
``PDFIUM_LOCK``.
"""

import math
from typing import Any

import pypdfium2.raw as pdfium_c

from anonymization_pipeline.layers import TextLayer, Token
from anonymization_pipeline.pdf.geometry import EMPTY, Geometry, Rect, union

# words written without space characters (scanner OCR layers): a gap wider than this fraction
# of the font size between two character boxes starts a new word
WORD_GAP = 0.12
# a baseline jump larger than this fraction of the font size starts a new line even when PDFium
# generated no line break
LINE_JUMP = 0.6
_LINE_BREAKS = {"\r", "\n", " ", " "}


class _Builder:
    """Accumulates characters into tokens and lines."""

    def __init__(self) -> None:
        self.tokens: list[Token] = []
        self.line = 0
        self.text: list[str] = []
        self.chars: list[Rect] = []
        self.last: Rect | None = None  # box of the last character of the current line
        self.horizontal = True

    def flush(self) -> None:
        if not self.text:
            return
        rect = EMPTY
        for c in self.chars:
            rect = union(rect, c)
        self.tokens.append(
            Token("".join(self.text), rect, list(self.chars), self.line, "native")
        )
        self.text.clear()
        self.chars.clear()

    def new_line(self) -> None:
        self.flush()
        if self.tokens and self.tokens[-1].line == self.line:
            self.line += 1
        self.last = None

    def add(self, ch: str, box: Rect, size: float, horizontal: bool) -> None:
        size = max(size, 1.0)
        if self.last is not None:
            if horizontal != self.horizontal or _jump(self.last, box, horizontal) > (
                LINE_JUMP * size
            ):
                self.new_line()
            elif self.chars and _gap(self.chars[-1], box, horizontal) > WORD_GAP * size:
                self.flush()
        self.horizontal = horizontal
        self.text.append(ch)
        self.chars.append(box)
        self.last = box


def _gap(prev: Rect, box: Rect, horizontal: bool) -> float:
    """Distance between two character boxes along the writing direction."""
    if horizontal:
        return max(box[0] - prev[2], prev[0] - box[2])
    return max(prev[1] - box[3], box[1] - prev[3])


def _jump(prev: Rect, box: Rect, horizontal: bool) -> float:
    """Distance between the centres of two character boxes across the writing direction."""
    if horizontal:
        return abs((prev[1] + prev[3]) / 2 - (box[1] + box[3]) / 2)
    return abs((prev[0] + prev[2]) / 2 - (box[0] + box[2]) / 2)


def _horizontal(textpage: Any, i: int, rotation: int) -> bool:
    """True when the character is written left to right (or right to left) on the screen."""
    angle = pdfium_c.FPDFText_GetCharAngle(textpage, i)
    if angle < 0:
        angle = 0.0
    shown = angle + math.radians(rotation)
    return abs(math.cos(shown)) >= abs(math.sin(shown))


def native_layer(page: Any, page_no: int) -> TextLayer:
    """Tokens of the PDF text layer with one display-space box per character.

    Args:
        page: An open ``pypdfium2.PdfPage``.
        page_no: Zero-based page number.
    """
    geo = Geometry(page)
    rotation = page.get_rotation()
    textpage = page.get_textpage()
    builder = _Builder()
    try:
        raw = textpage.raw
        n = pdfium_c.FPDFText_CountChars(raw)
        box = pdfium_c.FS_RECTF()
        i = 0
        while i < n:
            code = pdfium_c.FPDFText_GetUnicode(raw, i)
            step = 1
            if 0xD800 <= code <= 0xDBFF and i + 1 < n:
                low = pdfium_c.FPDFText_GetUnicode(raw, i + 1)
                if 0xDC00 <= low <= 0xDFFF:
                    code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
                    step = 2
            ch = chr(code) if code else ""
            if pdfium_c.FPDFText_HasUnicodeMapError(raw, i) == 1:
                ch = "�"  # kept: a manual box can still cover it
            if ch in _LINE_BREAKS:
                builder.new_line()
            elif not ch or ch.isspace():
                builder.flush()
            elif pdfium_c.FPDFText_GetLooseCharBox(raw, i, box):
                rect = geo.to_display(box.left, box.bottom, box.right, box.top)
                if step == 2:
                    second = pdfium_c.FS_RECTF()
                    if pdfium_c.FPDFText_GetLooseCharBox(raw, i + 1, second):
                        rect = union(
                            rect,
                            geo.to_display(
                                second.left, second.bottom, second.right, second.top
                            ),
                        )
                builder.add(
                    ch,
                    rect,
                    pdfium_c.FPDFText_GetFontSize(raw, i),
                    _horizontal(raw, i, rotation),
                )
            i += step
        builder.flush()
    finally:
        textpage.close()
    return TextLayer.build(page_no, "native", builder.tokens)
