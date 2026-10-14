"""Geometry and styles of native PDF pages, read from the text layer and the path objects.

Every visible character gives its box, baseline, size, fill color and font; the font is resolved
at once to a catalog family (``fonts.resolve``), its name is not kept. Path objects give lines and
rectangles; curves, oblique shapes, paths inside pictures and clusters of short parallel bars
(vector barcodes) or tiny squares (QR modules) are dropped: they can draw text or codes.

Coordinates are the ones of pdfium (and of docling's pypdfium2 backend): points, origin at the
bottom left of the page.
"""

import ctypes
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from anonymization_pipeline.ingest import PDFIUM_LOCK
from anonymization_pipeline.layout import fonts
from anonymization_pipeline.layout.model import MAX_GRAPHICS, Color, Graphic, Style

Box = tuple[float, float, float, float]  # left, bottom, right, top

_EPSILON = 0.01
MIN_EXTENT = (
    3.0  # graphics whose longest side is shorter are dropped (QR modules, dots)
)
BAR_WIDTH = 4.0  # a barcode bar is thinner than this...
BAR_LENGTH = 80.0  # ...and shorter than this
BAR_GAP = 6.0  # between two bars of the same code
MIN_BARS = 8  # bars that make a code


@dataclass(frozen=True)
class Glyph:
    """A visible character: advance box, baseline and style (index in ``Extraction.styles``)."""

    char: str
    x0: float
    x1: float
    bottom: float
    top: float
    baseline: float
    style: int


@dataclass
class PageGeometry:
    glyphs: list[Glyph] = field(default_factory=list)
    graphics: list[Graphic] = field(default_factory=list)


@dataclass
class Extraction:
    """Styles and geometry of the pages read, with counts (no values) for the metrics."""

    styles: list[Style] = field(default_factory=list)
    pages: dict[int, PageGeometry] = field(default_factory=dict)
    counts: Counter[str] = field(default_factory=Counter)


@dataclass
class _Styles:
    out: Extraction
    resolved: dict[int, fonts.Resolved] = field(default_factory=dict)
    index: dict[Style, int] = field(default_factory=dict)

    def font(self, handle: Any) -> fonts.Resolved:
        key = ctypes.cast(handle, ctypes.c_void_p).value or 0
        if key not in self.resolved:
            n = pdfium_c.FPDFFont_GetBaseFontName(handle, None, 0)
            buffer = ctypes.create_string_buffer(max(n, 1))
            pdfium_c.FPDFFont_GetBaseFontName(handle, buffer, n)
            angle = ctypes.c_int()
            pdfium_c.FPDFFont_GetItalicAngle(handle, angle)
            resolved = fonts.resolve(
                buffer.value.decode("latin-1"),
                max(pdfium_c.FPDFFont_GetFlags(handle), 0),
                pdfium_c.FPDFFont_GetWeight(handle),
                angle.value,
            )
            self.resolved[key] = resolved
            self.out.counts[f"font_{resolved.how}"] += 1
        return self.resolved[key]

    def add(self, style: Style) -> int:
        if style not in self.index:
            self.index[style] = len(self.out.styles)
            self.out.styles.append(style)
        return self.index[style]


def _color(r: int, g: int, b: int) -> Color:
    return (min(r, 255), min(g, 255), min(b, 255))


def _glyphs(textpage: Any, styles: _Styles) -> list[Glyph]:
    tp = textpage.raw
    out: list[Glyph] = []
    box = pdfium_c.FS_RECTF()
    matrix = pdfium_c.FS_MATRIX()
    x, y = ctypes.c_double(), ctypes.c_double()
    r, g, b, a = (ctypes.c_uint() for _ in range(4))
    for i in range(pdfium_c.FPDFText_CountChars(tp)):
        char = chr(pdfium_c.FPDFText_GetUnicode(tp, i))
        if char.isspace() or pdfium_c.FPDFText_IsGenerated(tp, i) == 1:
            continue
        obj = pdfium_c.FPDFText_GetTextObject(tp, i)
        if not obj or pdfium_c.FPDFTextObj_GetTextRenderMode(obj) in (
            pdfium_c.FPDF_TEXTRENDERMODE_INVISIBLE,
            pdfium_c.FPDF_TEXTRENDERMODE_CLIP,
        ):
            continue
        pdfium_c.FPDFText_GetMatrix(tp, i, matrix)
        # the font size of the text state, scaled by the text and the page matrices
        size = pdfium_c.FPDFText_GetFontSize(tp, i) * math.hypot(matrix.c, matrix.d)
        if size <= 0 or abs(pdfium_c.FPDFText_GetCharAngle(tp, i)) > _EPSILON:
            styles.out.counts["glyphs_skipped"] += 1  # rotated: not drawn upright
            continue
        handle = pdfium_c.FPDFTextObj_GetFont(obj)
        if not handle:
            continue
        pdfium_c.FPDFText_GetLooseCharBox(tp, i, box)
        pdfium_c.FPDFText_GetCharOrigin(tp, i, x, y)
        pdfium_c.FPDFText_GetFillColor(tp, i, r, g, b, a)
        resolved = styles.font(handle)
        style = styles.add(
            Style(
                font=resolved.font,
                bold=resolved.bold,
                italic=resolved.italic,
                size=round(min(size, 1000.0), 2),
                color=_color(r.value, g.value, b.value),
            )
        )
        out.append(
            Glyph(
                char,
                min(box.left, box.right),
                max(box.left, box.right),
                min(box.bottom, box.top),
                max(box.bottom, box.top),
                y.value,
                style,
            )
        )
    return out


def _subpaths(obj: Any) -> list[list[tuple[float, float]]] | None:
    """Points of every subpath, in page space; None if the path has curves."""
    raw = obj.raw
    matrix = pdfium_c.FS_MATRIX()
    pdfium_c.FPDFPageObj_GetMatrix(raw, matrix)
    if abs(matrix.b) > _EPSILON or abs(matrix.c) > _EPSILON:
        return None  # rotated or sheared
    out: list[list[tuple[float, float]]] = []
    fx, fy = ctypes.c_float(), ctypes.c_float()
    for k in range(pdfium_c.FPDFPath_CountSegments(raw)):
        segment = pdfium_c.FPDFPath_GetPathSegment(raw, k)
        kind = pdfium_c.FPDFPathSegment_GetType(segment)
        if kind == pdfium_c.FPDF_SEGMENT_BEZIERTO:
            return None
        pdfium_c.FPDFPathSegment_GetPoint(segment, fx, fy)
        point = (matrix.a * fx.value + matrix.e, matrix.d * fy.value + matrix.f)
        if kind == pdfium_c.FPDF_SEGMENT_MOVETO or not out:
            out.append([point])
        else:
            out[-1].append(point)
        if pdfium_c.FPDFPathSegment_GetClose(segment) and out[-1][0] != point:
            out[-1].append(out[-1][0])
    return out


def _axis(p: tuple[float, float], q: tuple[float, float]) -> bool:
    return abs(p[0] - q[0]) < _EPSILON or abs(p[1] - q[1]) < _EPSILON


def _is_rect(points: list[tuple[float, float]]) -> bool:
    closed = points if points[0] == points[-1] else [*points, points[0]]
    return (
        len(closed) == 5
        and all(_axis(p, q) for p, q in zip(closed, closed[1:], strict=False))
        and len({round(p[0], 2) for p in closed}) == 2
        and len({round(p[1], 2) for p in closed}) == 2
    )


def _object_color(getter: Any, raw: Any) -> Color | None:
    r, g, b, a = (ctypes.c_uint() for _ in range(4))
    if not getter(raw, r, g, b, a) or a.value == 0:
        return None
    return _color(r.value, g.value, b.value)


def _graphics(page: Any, counts: Counter[str]) -> list[Graphic]:
    out: list[Graphic] = []
    for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_PATH], max_depth=8):
        if getattr(obj, "level", 0) > 0:
            counts["graphics_dropped_form"] += (
                1  # inside a form: its own coordinate space
            )
            continue
        raw = obj.raw
        fill_mode, stroked = ctypes.c_int(), ctypes.c_int()
        pdfium_c.FPDFPath_GetDrawMode(raw, fill_mode, stroked)
        fill = (
            _object_color(pdfium_c.FPDFPageObj_GetFillColor, raw)
            if fill_mode.value
            else None
        )
        stroke = (
            _object_color(pdfium_c.FPDFPageObj_GetStrokeColor, raw)
            if stroked.value
            else None
        )
        if fill is None and stroke is None:
            continue
        subpaths = _subpaths(obj)
        if subpaths is None:
            counts["graphics_dropped_curve"] += 1
            continue
        width = ctypes.c_float()
        pdfium_c.FPDFPageObj_GetStrokeWidth(raw, width)
        matrix = pdfium_c.FS_MATRIX()
        pdfium_c.FPDFPageObj_GetMatrix(raw, matrix)
        line_width = min(width.value * max(abs(matrix.a), abs(matrix.d)), 100.0)
        shapes: list[Graphic] = []
        for points in subpaths:
            if len(points) >= 4 and _is_rect(points):
                xs, ys = [p[0] for p in points], [p[1] for p in points]
                shapes.append(
                    Graphic(
                        kind="rect",
                        x0=min(xs),
                        y0=min(ys),
                        x1=max(xs),
                        y1=max(ys),
                        stroke=stroke,
                        fill=fill,
                        width=line_width,
                    )
                )
            elif (
                stroke is not None
                and len(points) >= 2
                and all(_axis(p, q) for p, q in zip(points, points[1:], strict=False))
            ):
                shapes += [
                    Graphic(
                        kind="line",
                        x0=p[0],
                        y0=p[1],
                        x1=q[0],
                        y1=q[1],
                        stroke=stroke,
                        fill=None,
                        width=line_width,
                    )
                    for p, q in zip(points, points[1:], strict=False)
                    if p != q
                ]
            elif len(points) > 1:
                shapes = []
                counts["graphics_dropped_shape"] += 1
                break
        out += shapes
    return out


def _extent(g: Graphic) -> tuple[float, float]:
    return abs(g.x1 - g.x0), abs(g.y1 - g.y0)


def _overlaps(g: Graphic, box: Box) -> bool:
    left, bottom, right, top = box
    return (
        min(g.x0, g.x1) < right
        and max(g.x0, g.x1) > left
        and min(g.y0, g.y1) < top
        and max(g.y0, g.y1) > bottom
    )


def _bars(graphics: list[Graphic], vertical: bool) -> set[int]:
    """Indexes of the bars of vector barcodes: at least ``MIN_BARS`` short parallel bars side by
    side on the same band."""

    def span(g: Graphic) -> tuple[float, float, float, float]:
        # position along the code, start and end of the bar, across the code
        if vertical:
            return min(g.x0, g.x1), max(g.x0, g.x1), min(g.y0, g.y1), max(g.y0, g.y1)
        return min(g.y0, g.y1), max(g.y0, g.y1), min(g.x0, g.x1), max(g.x0, g.x1)

    candidates = []
    for i, g in enumerate(graphics):
        across, along = _extent(g) if vertical else _extent(g)[::-1]
        if across < BAR_WIDTH and along <= BAR_LENGTH:
            candidates.append(i)
    chains: list[list[int]] = []
    for i in sorted(candidates, key=lambda i: span(graphics[i])[0]):
        start, _, low, high = span(graphics[i])
        for chain in chains:
            _, end, chain_low, chain_high = span(graphics[chain[-1]])
            if (
                start - end < BAR_GAP
                and abs(low - chain_low) < 2
                and abs(high - chain_high) < 2
            ):
                chain.append(i)
                break
        else:
            chains.append([i])
    return {i for chain in chains if len(chain) >= MIN_BARS for i in chain}


def _inside(g: Graphic, box: Box) -> bool:
    left, bottom, right, top = box
    return (
        min(g.x0, g.x1) >= left - 1
        and max(g.x0, g.x1) <= right + 1
        and min(g.y0, g.y1) >= bottom - 1
        and max(g.y0, g.y1) <= top + 1
    )


def keep_graphics(
    graphics: list[Graphic],
    pictures: list[Box],
    counts: Counter[str],
    merged: list[Box] | None = None,
) -> list[Graphic]:
    """The graphics that can be drawn: none inside a picture or a row of character boxes joined
    into one text (its frames), no tiny shapes, no barcodes, and none at all over
    ``MAX_GRAPHICS``."""
    kept = []
    for g in graphics:
        if any(_overlaps(g, box) for box in pictures):
            counts["graphics_dropped_picture"] += 1
        elif any(_inside(g, box) for box in merged or []):
            counts["graphics_dropped_merged"] += 1
        elif max(_extent(g)) < MIN_EXTENT:
            counts["graphics_dropped_small"] += 1
        else:
            kept.append(g)
    bars = _bars(kept, vertical=True) | _bars(kept, vertical=False)
    counts["graphics_dropped_barcode"] += len(bars)
    kept = [g for i, g in enumerate(kept) if i not in bars]
    if len(kept) > MAX_GRAPHICS:
        counts["graphics_dropped_cap"] += len(kept)
        return []
    counts["graphics_kept"] += len(kept)
    return kept


def extract(
    data: bytes,
    page_numbers: set[int],
    graphics: bool,
    pictures: dict[int, list[Box]] | None = None,
    merged: dict[int, list[Box]] | None = None,
) -> Extraction:
    """Glyphs, styles and (with ``graphics``) lines and rectangles of the pages ``page_numbers``
    (one-based) of the PDF ``data``; no graphics over ``pictures`` or inside ``merged`` areas.

    Raises:
        pypdfium2.PdfiumError: The PDF cannot be read.
    """
    out = Extraction()
    styles = _Styles(out)
    pictures = pictures or {}
    with PDFIUM_LOCK:
        document = pdfium.PdfDocument(data)
        try:
            for page_no in sorted(page_numbers):
                if not 1 <= page_no <= len(document):
                    continue
                page = document[page_no - 1]
                textpage = page.get_textpage()
                try:
                    geometry = PageGeometry(_glyphs(textpage, styles))
                    if graphics:
                        geometry.graphics = keep_graphics(
                            _graphics(page, out.counts),
                            pictures.get(page_no, []),
                            out.counts,
                            (merged or {}).get(page_no, []),
                        )
                    out.pages[page_no] = geometry
                finally:
                    textpage.close()
                    page.close()
        finally:
            document.close()
    return out
