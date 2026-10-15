"""Rectangles and the two coordinate spaces of a PDF page.

Display space (used everywhere outside this module: text layers, OCR, detection, redaction
boxes, the UI): points, origin at the top-left corner of the page as it is shown, y downwards,
after ``/Rotate`` and the CropBox. Page space (PDFium text and page objects): unrotated PDF user
space, y upwards. Conversions use PDFium's own page-to-device transform, so they match the
rendered page exactly.
"""

import ctypes
from typing import Any

import pypdfium2.raw as pdfium_c

Rect = tuple[float, float, float, float]  # x0, y0, x1, y1

EMPTY: Rect = (0.0, 0.0, 0.0, 0.0)
# device units per point: PDFium transforms to integer device pixels
_SCALE = 1000


def is_empty(r: Rect) -> bool:
    """True when the rectangle has no area."""
    return r[2] <= r[0] or r[3] <= r[1]


def union(a: Rect, b: Rect) -> Rect:
    """Smallest rectangle containing both (an empty one is ignored)."""
    if is_empty(a):
        return b
    if is_empty(b):
        return a
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def union_all(rects: list[Rect]) -> Rect:
    """Smallest rectangle containing every rectangle of the list."""
    out = EMPTY
    for r in rects:
        out = union(out, r)
    return out


def intersect(a: Rect, b: Rect) -> Rect:
    """Common part of two rectangles (empty if they do not overlap)."""
    r = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    return EMPTY if is_empty(r) else r


def area(r: Rect) -> float:
    """Area in square points (0 for an empty rectangle)."""
    return 0.0 if is_empty(r) else (r[2] - r[0]) * (r[3] - r[1])


def intersects(a: Rect, b: Rect) -> bool:
    """True when the two rectangles share some area."""
    return not is_empty(intersect(a, b))


def grow(r: Rect, dx: float, dy: float | None = None) -> Rect:
    """The rectangle enlarged by ``dx`` horizontally and ``dy`` vertically on each side."""
    dy = dx if dy is None else dy
    return (r[0] - dx, r[1] - dy, r[2] + dx, r[3] + dy)


def center(r: Rect) -> tuple[float, float]:
    """Centre point of the rectangle."""
    return ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2)


def contains_point(r: Rect, x: float, y: float) -> bool:
    """True when the point is inside the rectangle (borders included)."""
    return r[0] <= x <= r[2] and r[1] <= y <= r[3]


class Geometry:
    """Converts between page space and display space for one open PDFium page.

    Args:
        page: A ``pypdfium2.PdfPage``; it must stay open while the object is used.
    """

    def __init__(self, page: Any) -> None:
        # the wrapper, not only its raw handle: pypdfium2 closes a page when it is collected
        self._page = page
        self._raw = page.raw
        self.width, self.height = page.get_size()  # display size, rotation applied

    def _device(self) -> tuple[int, int]:
        return round(self.width * _SCALE), round(self.height * _SCALE)

    def point_to_display(self, x: float, y: float) -> tuple[float, float]:
        """Page-space point -> display-space point."""
        w, h = self._device()
        dx, dy = ctypes.c_int(), ctypes.c_int()
        pdfium_c.FPDF_PageToDevice(self._raw, 0, 0, w, h, 0, x, y, dx, dy)
        return dx.value / _SCALE, dy.value / _SCALE

    def point_to_page(self, x: float, y: float) -> tuple[float, float]:
        """Display-space point -> page-space point."""
        w, h = self._device()
        px, py = ctypes.c_double(), ctypes.c_double()
        pdfium_c.FPDF_DeviceToPage(
            self._raw, 0, 0, w, h, 0, round(x * _SCALE), round(y * _SCALE), px, py
        )
        return px.value, py.value

    def to_display(self, left: float, bottom: float, right: float, top: float) -> Rect:
        """Page-space box (PDFium order: left, bottom, right, top) -> display rectangle."""
        x0, y0 = self.point_to_display(left, top)
        x1, y1 = self.point_to_display(right, bottom)
        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    def to_page(self, r: Rect) -> tuple[float, float, float, float]:
        """Display rectangle -> page-space box (left, bottom, right, top)."""
        x0, y0 = self.point_to_page(r[0], r[1])
        x1, y1 = self.point_to_page(r[2], r[3])
        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
