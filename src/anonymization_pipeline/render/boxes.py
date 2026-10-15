"""Redaction boxes: detected spans -> rectangles to remove from the PDF pages.

One box per line of every span. Native boxes are made of the glyph boxes of the characters (the
ink), so they never reach the next line, and get a small padding; OCR and manual boxes are
padded more, because OCR word boxes are approximate. Heavily overlapping boxes are merged, so tags
never print on top of each other. Rectangles are in display space.
"""

from dataclasses import dataclass, replace
from typing import Literal

from anonymization_pipeline.detection import Outcome
from anonymization_pipeline.layers import TextLayer
from anonymization_pipeline.pdf.geometry import (
    Rect,
    area,
    intersect,
    is_empty,
    union,
)

NATIVE_PAD = (
    0.5  # points added around native boxes (glyph boxes: the ink, nothing more)
)
PAD = 1.5  # points added around OCR and manual boxes
MERGE_OVERLAP = 0.4  # fraction of the smaller box that must overlap to merge two boxes


# how a page with boxes was redacted
Strategy = Literal["surgical", "flatten", "scan"]


class PageFallback(Exception):
    """A redaction strategy cannot redact a page: the next one is used."""


@dataclass(frozen=True)
class Box:
    """A rectangle to redact on a page."""

    page: int
    rect: Rect
    category: str
    source: str  # native | ocr | visual | manual
    horizontal: bool = True  # writing direction on the screen (native text)


def from_outcome(layers: list[TextLayer], outcome: Outcome) -> list[Box]:
    """One box per line of every detected span, with the writing direction of its line."""
    out: list[Box] = []
    for f in outcome.found:
        layer = layers[f.layer]
        for rect, horizontal in layer.line_rects(f.start, f.end):
            out.append(Box(layer.page, rect, f.category, layer.source, horizontal))
    return out


def _shaped(box: Box, page_rect: Rect) -> Box:
    """The box shaped by its source and clipped to the page.

    A box entirely outside the page (text the PDF holds but never shows) is kept as it is: the
    page still has to be redacted, otherwise that text would stay in the file.
    """
    x0, y0, x1, y1 = box.rect
    if box.source == "native":
        rect = (x0 - NATIVE_PAD, y0 - NATIVE_PAD, x1 + NATIVE_PAD, y1 + NATIVE_PAD)
    elif box.source in ("ocr", "manual"):
        rect = (x0 - PAD, y0 - PAD, x1 + PAD, y1 + PAD)
    else:
        rect = box.rect
    clipped = intersect(rect, page_rect)
    return replace(box, rect=rect if is_empty(clipped) else clipped)


def _merge(boxes: list[Box]) -> list[Box]:
    items = list(boxes)
    merged = True
    while merged:
        merged = False
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i], items[j]
                common = area(intersect(a.rect, b.rect))
                if common and common > MERGE_OVERLAP * min(area(a.rect), area(b.rect)):
                    keep = a if (a.source == "visual") >= (b.source == "visual") else b
                    items[i] = replace(keep, rect=union(a.rect, b.rect))
                    items.pop(j)
                    merged = True
                    break
            if merged:
                break
    return items


def prepare(boxes: list[Box], sizes: dict[int, tuple[float, float]]) -> list[Box]:
    """Boxes ready to apply: shaped by source, clipped to the page, overlapping ones merged.

    Args:
        boxes: Boxes of the whole document.
        sizes: Page number -> display width and height in points.
    """
    out: list[Box] = []
    for page in sorted({b.page for b in boxes}):
        width, height = sizes[page]
        page_rect = (0.0, 0.0, width, height)
        out += _merge([_shaped(b, page_rect) for b in boxes if b.page == page])
    return out
