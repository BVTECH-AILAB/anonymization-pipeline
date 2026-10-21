"""Pages of a PDF as the visual detectors see them, on pypdfium2.

``PageView`` gives the rendering (display space, as the page is shown), the
placements of the embedded images and the vector paths (page space, where text
and redaction boxes live). Objects nested in Form XObjects are placed on the
page through the matrices of their forms.
"""

import ctypes
import functools
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import numpy as np
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from anonymization_core.geometry import Rect

# PDFium is not thread-safe: two threads inside it at once crash the process
# (segmentation fault). Every use holds this lock: the worker analysing a
# document, request threads validating uploads and rendering previews.
PDFIUM_LOCK = threading.RLock()


def pdfium_locked[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Run ``fn`` holding ``PDFIUM_LOCK``: for functions that use PDFium throughout."""

    @functools.wraps(fn)
    def locked(*args: P.args, **kwargs: P.kwargs) -> R:
        with PDFIUM_LOCK:
            return fn(*args, **kwargs)

    return locked


MAX_FORM_DEPTH = 8
# resolution of the pages replaced by their rendering (and of their pixel check)
FLATTEN_DPI = 200


@dataclass
class Drawing:
    """One vector path of a page.

    Attributes:
        rect: Bounds in page space.
        kinds: One entry per segment: ``l`` (line), ``c`` (Bézier curve) or ``re``
            (an axis-aligned rectangle, one entry for its four sides).
        stroked: The path is stroked.
        filled: The path is filled.
        width: Stroke width in points.
    """

    rect: Rect
    kinds: list[str] = field(default_factory=list)
    stroked: bool = False
    filled: bool = False
    width: float = 0.0


def _segments(obj: pdfium.PdfObject) -> list[tuple[int, float, float, bool]]:
    out = []
    x, y = ctypes.c_float(), ctypes.c_float()
    for i in range(pdfium_c.FPDFPath_CountSegments(obj.raw)):
        seg = pdfium_c.FPDFPath_GetPathSegment(obj.raw, i)
        pdfium_c.FPDFPathSegment_GetPoint(seg, ctypes.byref(x), ctypes.byref(y))
        out.append(
            (
                pdfium_c.FPDFPathSegment_GetType(seg),
                x.value,
                y.value,
                bool(pdfium_c.FPDFPathSegment_GetClose(seg)),
            )
        )
    return out


def path_kinds(segments: list[tuple[int, float, float, bool]]) -> list[str]:
    """Segment kinds of a path: ``l`` line, ``c`` curve, ``re`` rectangle.

    Three ``BEZIERTO`` points make one curve; a subpath of four axis-aligned
    lines closing on its start is a rectangle (the PDF ``re`` operator).
    """
    kinds: list[str] = []
    sub: list[tuple[int, float, float, bool]] = []

    def flush() -> None:
        if not sub:
            return
        points = [(x, y) for _, x, y, _ in sub]
        lines = [s for s in sub[1:] if s[0] == pdfium_c.FPDF_SEGMENT_LINETO]
        closed = sub[-1][3] or points[-1] == points[0]
        if len(sub) in (4, 5) and len(lines) == len(sub) - 1 and closed:
            pts = points[:4]
            ring = [*pts, pts[0]]
            axis = all(
                a[0] == b[0] or a[1] == b[1]
                for a, b in zip(ring, ring[1:], strict=False)
            )
            if axis:
                kinds.append("re")
                return
        beziers = 0
        for kind, *_ in sub[1:]:
            if kind == pdfium_c.FPDF_SEGMENT_LINETO:
                kinds.append("l")
            elif kind == pdfium_c.FPDF_SEGMENT_BEZIERTO:
                beziers += 1
                if beziers % 3 == 0:
                    kinds.append("c")

    for s in segments:
        if s[0] == pdfium_c.FPDF_SEGMENT_MOVETO:
            flush()
            sub = [s]
        else:
            sub.append(s)
    flush()
    return kinds


class PageView:
    """One page of a pypdfium2 document.

    Args:
        page: The page.
        number: Its 0-based number.
    """

    def __init__(self, page: pdfium.PdfPage, number: int) -> None:
        self.page = page
        self.number = number
        self.rotation = page.get_rotation() % 360
        self.crop = page.get_cropbox()
        x0, y0, x1, y1 = self.crop
        self.page_width, self.page_height = x1 - x0, y1 - y0
        if self.rotation in (90, 270):
            self.width, self.height = self.page_height, self.page_width
        else:
            self.width, self.height = self.page_width, self.page_height

    @property
    def info(self) -> dict:
        """Geometry as in ``Analysis.pages`` (without ``scanned``)."""
        return {
            "width": self.width,
            "height": self.height,
            "rotation": self.rotation,
            "page_width": self.page_width,
            "page_height": self.page_height,
        }

    def render(self, dpi: float, clip: Rect | None = None) -> np.ndarray:
        """RGB rendering in display space, without annotations and form fields.

        Args:
            dpi: Resolution.
            clip: Area to render, in display space (points); None for the page.
        """
        crop = (0.0, 0.0, 0.0, 0.0)
        if clip is not None:
            x0, y0, x1, y1 = clip
            x0, y0 = max(0.0, x0), max(0.0, y0)
            x1, y1 = min(self.width, x1), min(self.height, y1)
            crop = (x0, self.height - y1, self.width - x1, y0)
        bitmap = self.page.render(
            scale=dpi / 72,
            crop=crop,
            draw_annots=False,
            may_draw_forms=False,
            rev_byteorder=True,
        )
        return np.ascontiguousarray(bitmap.to_numpy()[:, :, :3])

    def _to_page(self, bounds: tuple[float, float, float, float], chain: tuple) -> Rect:
        left, bottom, right, top = bounds
        points = [(left, bottom), (right, bottom), (left, top), (right, top)]
        for matrix in chain:
            points = [matrix.on_point(x, y) for x, y in points]
        xs = [x for x, _ in points]
        ys = [y for _, y in points]
        cx0, _, _, cy1 = self.crop
        return (min(xs) - cx0, cy1 - max(ys), max(xs) - cx0, cy1 - min(ys))

    def _walk(self, form=None, chain: tuple = (), depth: int = 0) -> Iterator[tuple]:
        for obj in self.page.get_objects(max_depth=0, form=form):
            yield obj, chain
            if obj.type == pdfium_c.FPDF_PAGEOBJ_FORM and depth < MAX_FORM_DEPTH:
                yield from self._walk(obj, (obj.get_matrix(), *chain), depth + 1)

    def images(self) -> list[Rect]:
        """Placements of the embedded raster images, in page space."""
        return [
            self._to_page(obj.get_bounds(), chain)
            for obj, chain in self._walk()
            if obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE
        ]

    def drawings(self) -> list[Drawing]:
        """Vector paths, in page space."""
        out = []
        fill, stroke = ctypes.c_int(), ctypes.c_int()
        width = ctypes.c_float()
        for obj, chain in self._walk():
            if obj.type != pdfium_c.FPDF_PAGEOBJ_PATH:
                continue
            pdfium_c.FPDFPath_GetDrawMode(
                obj.raw, ctypes.byref(fill), ctypes.byref(stroke)
            )
            pdfium_c.FPDFPageObj_GetStrokeWidth(obj.raw, ctypes.byref(width))
            scale = 1.0
            for m in (obj.get_matrix(), *chain):
                scale *= abs(m.a * m.d - m.b * m.c) ** 0.5
            out.append(
                Drawing(
                    self._to_page(obj.get_bounds(), chain),
                    path_kinds(_segments(obj)),
                    bool(stroke.value),
                    fill.value != 0,
                    width.value * scale,
                )
            )
        return out


def open_pages(pdf: pdfium.PdfDocument) -> list[PageView]:
    """Views of every page of ``pdf`` (the document must stay open while they are used)."""
    return [PageView(pdf[i], i) for i in range(len(pdf))]


def page_box(page: pdfium.PdfPage) -> tuple[float, float, float, float]:
    """CropBox in PDF user space (bottom-left origin)."""
    x0, y0, x1, y1 = page.get_cropbox()
    return x0, y0, x1, y1


def to_pdf(page: pdfium.PdfPage, rect: Rect) -> tuple[float, float, float, float]:
    """A page-space rectangle (top-left of the CropBox, unrotated) in PDF user space."""
    cx0, _, _, cy1 = page_box(page)
    x0, y0, x1, y1 = rect
    return cx0 + x0, cy1 - y1, cx0 + x1, cy1 - y0


def render_page(page: pdfium.PdfPage, scale: float) -> np.ndarray:
    """RGB rendering as displayed, without annotations and form fields (as the output shows it)."""
    bitmap = page.render(
        scale=scale, draw_annots=False, may_draw_forms=False, rev_byteorder=True
    )
    return np.ascontiguousarray(bitmap.to_numpy()[:, :, :3])
