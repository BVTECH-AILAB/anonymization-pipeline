"""Glyphs of the PDF bound to the text fields of the structure (text items and table cells).

The glyphs inside the box of a field are matched, in content order, with the characters of its
text (spaces excluded, NFKC: ligatures match their letters). A field with too few matches keeps
no layout and is drawn in its box as before. The result is IN CLEAR TEXT (it holds the position
of every character): ``censor`` turns it into the layout returned with the document.
"""

import bisect
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher

from docling_core.types.doc import BoundingBox, DoclingDocument

from anonymization_pipeline.layout.pdf import Box, Extraction, Glyph
from anonymization_pipeline.redaction.structure import Field

MIN_COVERAGE = 0.9  # matched characters of a field, below it the field keeps no layout
MARGIN = 2.0  # points around a field box
MAX_CHARS = 20000  # longer fields are not aligned (quadratic matching)
_WORD = re.compile(r"\S+")


@dataclass
class ClearRun:
    """Characters ``[start, end)`` of a field on one baseline; ``xs`` holds the ``end - start + 1``
    boundaries of the characters (left edges, then the right edge of the last one)."""

    start: int
    end: int
    baseline: float
    style: int
    xs: list[float]


@dataclass
class ClearField:
    page_no: int
    runs: list[ClearRun]


def _box(bbox: BoundingBox, page_height: float) -> Box:
    b = bbox.to_bottom_left_origin(page_height)
    return (
        b.l - MARGIN,
        min(b.b, b.t) - MARGIN,
        b.r + MARGIN,
        max(b.b, b.t) + MARGIN,
    )


class _Page:
    """Glyphs of a page sorted by the vertical center of their box, for band lookups."""

    def __init__(self, glyphs: list[Glyph]) -> None:
        order = sorted(range(len(glyphs)), key=lambda i: _center(glyphs[i])[1])
        self.glyphs = glyphs
        self.order = order
        self.keys = [_center(glyphs[i])[1] for i in order]

    def inside(self, boxes: list[Box]) -> list[int]:
        """Indexes (content order) of the glyphs whose center is in one of ``boxes``."""
        found: set[int] = set()
        for left, bottom, right, top in boxes:
            lo = bisect.bisect_left(self.keys, bottom)
            hi = bisect.bisect_right(self.keys, top)
            for i in self.order[lo:hi]:
                x = _center(self.glyphs[i])[0]
                if left <= x <= right:
                    found.add(i)
        return sorted(found)


def _center(g: Glyph) -> tuple[float, float]:
    return (g.x0 + g.x1) / 2, (g.bottom + g.top) / 2


def _expanded(chars: list[tuple[int, str]]) -> tuple[list[int], list[str]]:
    sources: list[int] = []
    out: list[str] = []
    for source, char in chars:
        for c in unicodedata.normalize("NFKC", char):
            if not c.isspace():
                sources.append(source)
                out.append(c)
    return sources, out


def _matches(
    text: str, glyphs: list[Glyph], picked: list[int]
) -> dict[int, int] | None:
    """Text index -> glyph index; None under ``MIN_COVERAGE``."""
    text_sources, text_chars = _expanded(list(enumerate(text)))
    if not text_chars or len(text_chars) > MAX_CHARS:
        return None
    glyph_sources, glyph_chars = _expanded([(i, glyphs[i].char) for i in picked])
    matcher = SequenceMatcher(None, text_chars, glyph_chars, autojunk=False)
    out: dict[int, int] = {}
    matched = 0
    for a, b, n in matcher.get_matching_blocks():
        matched += n
        for k in range(n):
            out.setdefault(text_sources[a + k], glyph_sources[b + k])
    return out if matched >= MIN_COVERAGE * len(text_chars) else None


def _runs(
    text: str, glyphs: list[Glyph], matches: dict[int, int], sizes: list[float]
) -> list[ClearRun] | None:
    """One run per word and line; None if a word has no glyph at all."""
    out: list[ClearRun] = []
    for word in _WORD.finditer(text):
        positions = range(word.start(), word.end())
        if not any(k in matches for k in positions):
            return None
        current: list[tuple[int, Glyph | None]] = []
        pieces: list[list[tuple[int, Glyph | None]]] = [current]
        last: Glyph | None = None
        for k in positions:
            g = glyphs[matches[k]] if k in matches else None
            if g is not None and last is not None:
                tolerance = 0.3 * sizes[last.style]
                if (
                    abs(g.baseline - last.baseline) > tolerance
                    or g.style != last.style
                    or g.x0 < last.x0 - 0.5
                ):
                    current = []
                    pieces.append(current)
            current.append((k, g))
            last = g or last
        for piece in pieces:  # a piece after a break starts with a glyph
            known = [g for _, g in piece if g is not None]
            xs: list[float] = []
            for _, g in piece:
                xs.append(g.x0 if g is not None else (xs[-1] if xs else known[0].x0))
            xs.append(known[-1].x1)
            out.append(
                ClearRun(
                    start=piece[0][0],
                    end=piece[-1][0] + 1,
                    baseline=known[0].baseline,
                    style=known[0].style,
                    xs=xs,
                )
            )
    return out


def _fields(structure: DoclingDocument) -> list[tuple[Field, str, int, list[Box]]]:
    """Every field with its page and boxes; fields on several pages are left out."""
    out: list[tuple[Field, str, int, list[Box]]] = []
    for item in structure.texts:
        pages = {p.page_no for p in item.prov}
        if len(pages) == 1:
            page_no = pages.pop()
            height = structure.pages[page_no].size.height
            boxes = [_box(p.bbox, height) for p in item.prov]
            out.append(((item.self_ref, None), item.text, page_no, boxes))
    for table in structure.tables:
        if not table.prov:
            continue
        page_no = table.prov[0].page_no
        height = structure.pages[page_no].size.height
        for i, cell in enumerate(table.data.table_cells):
            if cell.bbox is not None:
                out.append(
                    (
                        (table.self_ref, i),
                        cell.text,
                        page_no,
                        [_box(cell.bbox, height)],
                    )
                )
    return out


def align(
    structure: DoclingDocument, extraction: Extraction, counts: Counter[str]
) -> dict[Field, ClearField]:
    """The fields of ``structure`` (IN CLEAR TEXT) on the pages of ``extraction`` with their runs."""
    pages = {n: _Page(g.glyphs) for n, g in extraction.pages.items()}
    sizes = [s.size for s in extraction.styles]
    out: dict[Field, ClearField] = {}
    for where, text, page_no, boxes in _fields(structure):
        if page_no not in pages or not text.strip():
            continue
        page = pages[page_no]
        picked = page.inside(boxes)
        matches = _matches(text, page.glyphs, picked) if picked else None
        runs = _runs(text, page.glyphs, matches, sizes) if matches else None
        if runs:
            out[where] = ClearField(page_no, runs)
        else:
            counts["fields_fallback"] += 1
    return out
