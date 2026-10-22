"""Characters of a PDF text layer, with boxes like a PDF reader draws them (pypdfium2).

Each box runs along the baseline over the character's advance, from the font's
descender to its ascender: the whole glyph, accents and tails included, so a
redaction box built from them covers what the page shows.
Page space: points, top-left origin of the CropBox, page not rotated.
"""

import ctypes
import math
from dataclasses import dataclass

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from src.services.anonymizer.geometry import Rect

# ascender and descender (em units) of the standard 14 fonts as most PDF
# readers substitute them (URW base 35 metrics); a font that is not embedded
# has no other source for them
BASE14_METRICS = {
    "Helvetica": (1.075, -0.299),
    "Helvetica-Bold": (1.07, -0.307),
    "Helvetica-Oblique": (1.07, -0.284),
    "Helvetica-BoldOblique": (1.073, -0.309),
    "Times-Roman": (1.053, -0.281),
    "Times-Bold": (1.044, -0.341),
    "Times-Italic": (0.951, -0.27),
    "Times-BoldItalic": (0.972, -0.324),
    "Courier": (0.932, -0.317),
    "Courier-Bold": (1.007, -0.393),
    "Courier-Oblique": (0.92, -0.317),
    "Courier-BoldOblique": (0.997, -0.393),
    "Symbol": (0.819, -0.144),
    "ZapfDingbats": (0.819, -0.144),
}


@dataclass
class Char:
    """One character of a page's text layer.

    Attributes:
        text: The character.
        rect: Loose box (font ascent to descent) in page space.
        rgb: Fill colour as ``0xRRGGBB``.
        generated: Inserted by pdfium (a space or line break that is not in the file).
        angle: Rotation of the character, radians counter-clockwise.
        size: Font size in points.
    """

    text: str
    rect: Rect
    rgb: int
    generated: bool
    angle: float = 0.0
    size: float = 0.0


def to_page(page: pdfium.PdfPage, bounds: tuple[float, float, float, float]) -> Rect:
    """PDF user-space bounds ``(left, bottom, right, top)`` as a page-space rectangle."""
    cx0, _, _, cy1 = page.get_cropbox()
    left, bottom, right, top = bounds
    return (left - cx0, cy1 - top, right - cx0, cy1 - bottom)


def _font_metrics(font, cache: dict) -> tuple[float, float]:
    """Ascender and descender of a font in em units."""
    key = ctypes.cast(font, ctypes.c_void_p).value
    if key not in cache:
        buf = ctypes.create_string_buffer(256)
        pdfium_c.FPDFFont_GetBaseFontName(font, buf, 256)
        name = buf.value.decode("latin-1").split("+")[-1]
        if name in BASE14_METRICS and not pdfium_c.FPDFFont_GetIsEmbedded(font):
            cache[key] = BASE14_METRICS[name]
        else:
            asc, desc = ctypes.c_float(), ctypes.c_float()
            pdfium_c.FPDFFont_GetAscent(font, ctypes.c_float(1.0), ctypes.byref(asc))
            pdfium_c.FPDFFont_GetDescent(font, ctypes.c_float(1.0), ctypes.byref(desc))
            cache[key] = (asc.value, desc.value)
    return cache[key]


def _char_box(textpage: pdfium.PdfTextPage, i: int, cache: dict) -> tuple:
    """PDF-space box of one character: its advance along the baseline, from the
    font's descender to its ascender across it (pdfium's loose box when the font
    is unknown).

    The loose box of rotated text is the axis-aligned bound of a rotated
    rectangle: its advance is recovered from the box and the angle.
    """
    raw = textpage.raw
    rect = pdfium_c.FS_RECTF()
    pdfium_c.FPDFText_GetLooseCharBox(raw, i, ctypes.byref(rect))
    loose = (rect.left, rect.bottom, rect.right, rect.top)
    obj = pdfium_c.FPDFText_GetTextObject(raw, i)
    font = pdfium_c.FPDFTextObj_GetFont(obj) if obj else None
    if not font:
        return loose
    asc, desc = _font_metrics(font, cache)
    if asc - desc <= 0:
        return loose
    x, y = ctypes.c_double(), ctypes.c_double()
    pdfium_c.FPDFText_GetCharOrigin(raw, i, ctypes.byref(x), ctypes.byref(y))
    size = pdfium_c.FPDFText_GetFontSize(raw, i)
    angle = pdfium_c.FPDFText_GetCharAngle(raw, i)
    cos, sin = math.cos(angle), -math.sin(angle)  # pdfium turns clockwise
    if abs(sin) < 1e-3:
        if cos > 0:
            return (rect.left, y.value + desc * size, rect.right, y.value + asc * size)
        return (rect.left, y.value - asc * size, rect.right, y.value - desc * size)
    bw, bh = rect.right - rect.left, rect.top - rect.bottom
    ac, as_ = abs(cos), abs(sin)
    if abs(ac - as_) > 0.2:
        advance = max(0.0, (bw * ac - bh * as_) / (ac * ac - as_ * as_))
    else:
        advance = bw / ac
    cx, cy = (
        (rect.left + rect.right) / 2 - x.value,
        (rect.bottom + rect.top) / 2 - y.value,
    )
    t0 = cx * cos + cy * sin - advance / 2
    xs, ys = [], []
    for t in (t0, t0 + advance):
        for n in (desc * size, asc * size):
            xs.append(x.value + t * cos - n * sin)
            ys.append(y.value + t * sin + n * cos)
    return (min(xs), min(ys), max(xs), max(ys))


def chars(page: pdfium.PdfPage) -> list[Char]:
    """Every character of the page, in reading order, with box and fill colour."""
    textpage = page.get_textpage()
    r, g, b, a = (ctypes.c_uint() for _ in range(4))
    cache: dict = {}
    out = []
    try:
        for i in range(textpage.count_chars()):
            code = pdfium_c.FPDFText_GetUnicode(textpage.raw, i)
            rgb = 0
            if pdfium_c.FPDFText_GetFillColor(
                textpage.raw,
                i,
                ctypes.byref(r),
                ctypes.byref(g),
                ctypes.byref(b),
                ctypes.byref(a),
            ):
                rgb = (r.value << 16) | (g.value << 8) | b.value
            out.append(
                Char(
                    chr(code) if 0 < code < 0x110000 else "",
                    to_page(page, _char_box(textpage, i, cache)),
                    rgb,
                    bool(pdfium_c.FPDFText_IsGenerated(textpage.raw, i)),
                    -pdfium_c.FPDFText_GetCharAngle(textpage.raw, i),
                    pdfium_c.FPDFText_GetFontSize(textpage.raw, i),
                )
            )
    finally:
        textpage.close()
    return out
