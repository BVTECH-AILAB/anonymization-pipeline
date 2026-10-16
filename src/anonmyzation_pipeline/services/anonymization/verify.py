"""Fail-closed verification of a redacted PDF.

1. under every box no original text survives (only our tag)
2. no redacted value appears anywhere in the output text, raw (decompressed)
   bytes or metadata
3. preservation: rendered pages are identical outside the boxes
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pymupdf
from docx import Document

from anonmyzation_pipeline.services.anonymization.layers import is_vertical
from anonmyzation_pipeline.services.anonymization.pipeline import Analysis
from anonmyzation_pipeline.services.anonymization.taxonomy import LABELS, STRONG_LABELS

# values strong enough to search globally (single surnames can collide with eponyms)
_TAGS = {w for lab in LABELS.values() for t in (lab.tag, lab.short) for w in t.split()}


@dataclass
class Report:
    ok: bool = True
    issues: list[str] = field(default_factory=list)
    pages: set[int] = field(default_factory=set)  # 0-based pages with a failed check
    whole_file: bool = False  # a check failed on the file, not on one page
    pixel_changed_outside: list[float] = field(
        default_factory=list
    )  # per page, fraction of pixels

    def fail(self, msg: str, page: int | None = None) -> None:
        """Record a failed check: the document is not released.

        Args:
            msg: What failed, shown to the uploader.
            page: The 0-based page it failed on, or None for the whole file.
        """
        self.ok = False
        self.issues.append(msg if page is None else f"p{page + 1}: {msg}")
        if page is None:
            self.whole_file = True
        else:
            self.pages.add(page)


def render_matrix(page: pymupdf.Page, dpi: int) -> pymupdf.Matrix:
    """The scale that renders ``page`` at ``dpi`` with a whole number of pixels per side.

    A flattened page is an image of exactly that many pixels: rendered with this
    matrix, one image pixel is one rendered pixel, with no resampling to blur the
    comparison (at 200 dpi a 595.5 pt page is 1654.2 pixels wide, not 1655).
    """
    size = (page.rect * pymupdf.Matrix(dpi / 72, dpi / 72)).irect
    return pymupdf.Matrix(size.width / page.rect.width, size.height / page.rect.height)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def strong_values(analysis: Analysis) -> set[str]:
    """Redacted values that must not appear anywhere in an output: identifiers and full names."""
    boxes = [x for x in analysis.boxes if x.redact]
    values = {
        x.text for x in boxes if x.label in STRONG_LABELS and len(_norm(x.text)) >= 6
    }
    values |= {
        x.text for x in boxes if x.label == "PERSON" and len(x.text.split()) >= 2
    }
    # an occurrence the user chose to keep visible is not a leak
    return values - {
        x.text
        for x in analysis.boxes
        if not x.redact and "removed-by-user" in x.sources
    }


def verify_docx(path: Path, analysis: Analysis) -> list[str]:
    """Issues of the Word export: a redacted value left in its text.

    Returns:
        One message per value found; empty when the export is clean.
    """
    text = _norm(" ".join(p.text for p in Document(str(path)).paragraphs))
    return [
        f"value still in the Word export: '{v}'"
        for v in strong_values(analysis)
        if _norm(v) in text
    ]


def verify(
    src: Path,
    out: Path,
    analysis: Analysis,
    dpi: int = 72,
    tol: float = 2e-4,
    raster_pages: dict[int, int] | None = None,
) -> Report:
    """Fail-closed checks of ``out`` against ``src``: nothing left under the boxes, no value or
    text outside the page left in the file, pixels unchanged outside the boxes."""
    raster_pages = raster_pages or {}  # page -> dpi of pages replaced by a rendering
    rep = Report()
    a, b = pymupdf.open(src), pymupdf.open(out)
    try:
        boxes = [x for x in analysis.boxes if x.redact]

        # 1. nothing left under the boxes
        for x in boxes:
            if x.layer == "visual":
                continue
            r = pymupdf.Rect(x.rect)
            if is_vertical(r, x.text):
                r = pymupdf.Rect(
                    r.x0 + r.width * 0.3, r.y0 + 1, r.x1 - r.width * 0.3, r.y1 - 1
                )
            else:
                r = pymupdf.Rect(
                    r.x0 + 1, r.y0 + r.height * 0.3, r.x1 - 1, r.y1 - r.height * 0.3
                )
            left = [
                w
                for w in b[x.page].get_text("words", clip=r)
                if w[4] not in _TAGS and pymupdf.Rect(w[:4]).intersects(r)
            ]
            orig = _norm(x.text)
            for w in left:
                if len(w[4]) >= 2 and _norm(w[4]) in orig:
                    rep.fail(f"'{w[4]}' still under {x.label} box", x.page)

        # 1b. text outside the visible page area: invisible, never detected, still in the file
        for pno in range(b.page_count):
            page = b[pno]
            visible = page.rect * page.derotation_matrix
            hidden = [
                w
                for w in page.get_text("words", clip=pymupdf.INFINITE_RECT())
                if not pymupdf.Rect(w[:4]).intersects(visible)
            ]
            if hidden:
                rep.fail(f"{len(hidden)} words outside the visible page area", pno)

        # 2. global search for strong values
        full = _norm(" ".join(p.get_text() for p in b))
        raw = b"".join(
            (b.xref_stream(i) or b"") + b.xref_object(i).encode("latin-1", "ignore")
            for i in range(1, b.xref_length())
        )
        raw_low = raw.lower()
        md = " ".join(str(v) for v in (b.metadata or {}).values() if v) + (
            b.get_xml_metadata() or ""
        )
        values = strong_values(analysis)
        for v in values:
            nv = _norm(v)
            if nv in full:
                rep.fail(f"value still in text: '{v}'")
            if v.lower().encode("latin-1", "ignore") in raw_low and len(v) >= 6:
                rep.fail(f"value still in file bytes: '{v}'")
            if nv and nv in _norm(md):
                rep.fail(f"value still in metadata: '{v}'")
        for pno in range(b.page_count):
            if list(b[pno].annots() or []):
                rep.fail("annotations left", pno)
        # bookmarks and the structure tree carry text outside the pages
        if b.get_toc():
            rep.fail("bookmarks left")
        cat = b.pdf_catalog()
        for key in ("StructTreeRoot", "Names"):
            if b.xref_get_key(cat, key)[0] != "null":
                rep.fail(f"document entry left: {key}")

        # 3. preservation outside the boxes
        for pno in range(min(a.page_count, b.page_count)):
            pa, pb = a[pno], b[pno]
            matrix = render_matrix(pa, raster_pages.get(pno, dpi))
            ia = pa.get_pixmap(matrix=matrix, alpha=False, annots=False)
            ib = pb.get_pixmap(matrix=matrix, alpha=False, annots=False)
            A = (
                np.frombuffer(ia.samples, np.uint8)
                .reshape(ia.height, ia.width, ia.n)
                .astype(np.int16)
            )
            B = (
                np.frombuffer(ib.samples, np.uint8)
                .reshape(ib.height, ib.width, ib.n)
                .astype(np.int16)
            )
            diff = np.abs(A - B).max(axis=2) > 40
            sx, sy = matrix.a, matrix.d
            rects = [r for n, r, _ in analysis.applied if n == pno] or [
                x.rect for x in boxes if x.page == pno
            ]
            # boxes are in unrotated coordinates, the rendering is rotated
            rects = [tuple(pymupdf.Rect(r) * pa.rotation_matrix) for r in rects]
            for x0, y0, x1, y1 in rects:
                diff[
                    max(0, int((y0 - 3) * sy)) : int((y1 + 3) * sy) + 1,
                    max(0, int((x0 - 3) * sx)) : int((x1 + 3) * sx) + 1,
                ] = False
            frac = float(diff.mean())
            rep.pixel_changed_outside.append(frac)
            if frac > tol:
                rep.fail(f"{frac:.2%} of pixels changed outside the boxes", pno)
        return rep
    finally:
        a.close()
        b.close()
