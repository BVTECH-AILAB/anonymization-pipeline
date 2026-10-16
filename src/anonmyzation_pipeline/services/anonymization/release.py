"""Redact, verify, and replace only the pages that fail with a rendering of themselves.

Surgical redaction keeps each page native (selectable text, identical layout).
When a page fails verification (text left under a box, text outside the visible
area, pixels changed around a box) the page is rendered with the boxes painted
in and replaced by that image: nothing of its content survives but the pixels,
which are compared with the original at the same resolution. The document is
verified again and released only if every check passes (ported from the
flatten fallback of feat/reconstructed-architecture). A document that still
fails stays blocked.
"""

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from anonmyzation_pipeline.services.anonymization.pipeline import Analysis
from anonmyzation_pipeline.services.anonymization.redact import (
    STYLES,
    redact,
    scrub_document,
)
from anonmyzation_pipeline.services.anonymization.verify import (
    Report,
    render_matrix,
    strong_values,
    verify,
)

logger = logging.getLogger(__name__)

FLATTEN_DPI = 200


@dataclass
class Release:
    """Outcome of ``redact_and_verify``."""

    report: Report
    flattened: list[int] = field(
        default_factory=list
    )  # 0-based pages released as images
    seconds: dict[str, float] = field(default_factory=dict)  # redaction, verification


def _failing_pages(out: Path, analysis: Analysis, rep: Report) -> set[int]:
    """Pages to flatten: those a check failed on, and those still holding a redacted value."""
    pages = set(rep.pages)
    if rep.whole_file:
        # a value left somewhere in the file: flatten the pages that show it (the
        # document-level leftovers are scrubbed again with them)
        values = [re.sub(r"\s+", "", v).lower() for v in strong_values(analysis)]
        with pymupdf.open(out) as doc:
            for pno, page in enumerate(doc):
                text = re.sub(r"\s+", "", page.get_text()).lower()
                if any(v and v in text for v in values):
                    pages.add(pno)
    return pages


def flatten_pages(
    src: Path, analysis: Analysis, out: Path, pages: set[int], style: str = "black"
) -> None:
    """Replace ``pages`` of ``out`` with renderings of ``src`` with the boxes painted in.

    The painted rectangles replace those pages' entries in ``analysis.applied``:
    they are what the pixel check must ignore on the flattened pages.
    """
    fill = STYLES[style][0]
    painted: list[tuple[int, tuple[float, float, float, float], str]] = []
    with pymupdf.open(src) as original, pymupdf.open(out) as doc:
        for pno in sorted(pages):
            page = original[pno]
            # every box to redact, whatever the surgical step managed to apply
            for b in analysis.boxes:
                if b.page == pno and b.redact:
                    rect = pymupdf.Rect(b.rect) + (-1, -1, 1, 1)
                    page.draw_rect(rect, color=None, fill=fill, overlay=True)
                    painted.append((pno, tuple(rect), b.label))
            # the scale the pixel check renders at: one image pixel per rendered pixel
            matrix = render_matrix(page, FLATTEN_DPI)
            png = page.get_pixmap(matrix=matrix, alpha=False, annots=False).tobytes(
                "png"
            )
            width, height = page.rect.width, page.rect.height  # as displayed
            doc.delete_page(pno)
            flat = doc.new_page(pno=pno, width=width, height=height)
            flat.insert_image(flat.rect, stream=png)
        scrub_document(doc)  # again: the file may still hold data outside the pages
        doc.save(out.with_suffix(".flat.pdf"), garbage=4, deflate=True, clean=True)
    out.with_suffix(".flat.pdf").replace(out)
    analysis.applied = [a for a in analysis.applied if a[0] not in pages] + painted


def redact_and_verify(
    src: Path,
    analysis: Analysis,
    out: Path,
    style: str = "black",
    tol: float = 2e-4,
) -> Release:
    """Surgical redaction of ``src`` into ``out``; failing pages are flattened and checked again.

    Returns:
        The final verification report, the flattened pages and the time spent
        redacting (flattening included) and verifying.
    """
    t0 = time.perf_counter()
    redact(src, analysis, out, style=style)
    t1 = time.perf_counter()
    rep = verify(src, out, analysis, tol=tol)
    t2 = time.perf_counter()
    seconds = {"redaction": t1 - t0, "verification": t2 - t1}
    pages = set() if rep.ok else _failing_pages(out, analysis, rep)
    if pages:
        flatten_pages(src, analysis, out, pages, style)
        t3 = time.perf_counter()
        rep = verify(
            src, out, analysis, tol=tol, raster_pages=dict.fromkeys(pages, FLATTEN_DPI)
        )
        seconds["redaction"] += t3 - t2
        seconds["verification"] += time.perf_counter() - t3
        logger.info(
            "Pages flattened after a failed check: pages=%d verified=%s",
            len(pages),
            rep.ok,
        )
    return Release(rep, sorted(pages), {k: round(v, 2) for k, v in seconds.items()})
