"""What happens to an analysed document: releases, manual edits, page images.

Releases are fail-closed: every file is written next to its destination and
moved into place only when all of them exist, so a failure (or a progress
callback that raises to cancel) leaves the previous version untouched and no
temporary file behind. Whether a release may be downloaded is the caller's
decision, from ``verified``.
"""

import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium

from anonymization_core.errors import PageNotFoundError
from anonymization_core.export_docx import export_docx
from anonymization_core.export_markdown import (
    export_markdown,
)
from anonymization_core.pdf_pages import pdfium_locked
from anonymization_core.pipeline import (
    Analysis,
    Box,
    Progress,
    from_display,
    to_display,
)
from anonymization_core.release import redact_and_verify
from anonymization_core.verify import (
    verify_docx,
    verify_markdown,
)

DisplayRect = tuple[float, float, float, float]


@dataclass
class PdfRelease:
    """Outcome of ``release_pdf``.

    Attributes:
        verified: Both the PDF and the Word copy passed verification.
        issues: What failed verification (no document text).
        flattened: 0-based pages released as images (a surgical check failed).
        seconds: ``redaction`` and ``verification`` times.
    """

    verified: bool
    issues: list[str]
    flattened: list[int]
    seconds: dict[str, float]


@dataclass
class MarkdownRelease:
    """Outcome of ``release_markdown``: verification and its time in seconds."""

    verified: bool
    issues: list[str]
    seconds: float


@dataclass
class EditOutcome:
    """What ``apply_edits`` changed: areas added and search hits redacted."""

    added: int
    search_hits: int


def _no_progress(step: str, fraction: float) -> None:
    """Progress callback that ignores every update."""


def _next(path: Path) -> Path:
    """Temporary sibling of ``path``: ``anonymized.pdf`` -> ``anonymized.next.pdf``."""
    return path.with_suffix(".next" + path.suffix)


def release_pdf(
    original: Path,
    analysis: Analysis,
    pdf_dest: Path,
    docx_dest: Path,
    *,
    style: str,
    tol: float,
    progress: Progress | None = None,
) -> PdfRelease:
    """Redact the PDF and export the Word copy, then verify both.

    Args:
        original: The source PDF.
        analysis: Its analysis, boxes included.
        pdf_dest: Where the redacted PDF goes.
        docx_dest: Where the Word copy goes.
        style: ``black`` or ``label`` boxes.
        tol: Share of rendered pixels allowed to change outside the boxes.
        progress: Receives the ``redact`` and ``preview`` steps.
    """
    report = progress or _no_progress
    pdf_next, docx_next = _next(pdf_dest), _next(docx_dest)
    try:
        report("redact", 0)
        release = redact_and_verify(original, analysis, pdf_next, style=style, tol=tol)
        report("preview", 0)
        t1 = time.perf_counter()
        export_docx(analysis, docx_next)
        docx_issues = verify_docx(docx_next, analysis)
        verification = release.seconds["verification"] + time.perf_counter() - t1
    except BaseException:
        pdf_next.unlink(missing_ok=True)
        docx_next.unlink(missing_ok=True)
        raise
    os.replace(pdf_next, pdf_dest)
    os.replace(docx_next, docx_dest)
    return PdfRelease(
        verified=release.report.ok and not docx_issues,
        issues=release.report.issues + docx_issues,
        flattened=release.flattened,
        seconds={
            "redaction": release.seconds["redaction"],
            "verification": round(verification, 2),
        },
    )


def release_markdown(
    analysis: Analysis, dest: Path, progress: Progress | None = None
) -> MarkdownRelease:
    """Export the anonymized text as Markdown and verify it.

    Args:
        analysis: The analysed document.
        dest: Where the Markdown goes.
        progress: Receives the ``export`` step.
    """
    (progress or _no_progress)("export", 0)
    t1 = time.perf_counter()
    md_next = _next(dest)
    try:
        export_markdown(analysis, md_next)
        issues = verify_markdown(md_next, analysis)
    except BaseException:
        md_next.unlink(missing_ok=True)
        raise
    os.replace(md_next, dest)
    return MarkdownRelease(
        verified=not issues,
        issues=issues,
        seconds=round(time.perf_counter() - t1, 2),
    )


def apply_edits(
    analysis: Analysis,
    add: list[tuple[int, DisplayRect]] | None = None,
    remove: list[int] | None = None,
    restore: list[int] | None = None,
    search: str = "",
) -> EditOutcome:
    """Apply manual corrections to the boxes of ``analysis`` (no new detection).

    Unknown box ids and pages are ignored.

    Args:
        analysis: Edited in place.
        add: ``(page, rect)`` areas to redact, in display space.
        remove: Box ids that must not be redacted.
        restore: Box ids to redact again.
        search: Text whose every occurrence is redacted ("" for none).
    """
    boxes = analysis.boxes
    for i in remove or []:
        if 0 <= i < len(boxes):
            boxes[i].redact = False
            boxes[i].sources = sorted(set(boxes[i].sources) | {"removed-by-user"})
    for i in restore or []:
        if 0 <= i < len(boxes):
            boxes[i].redact = True
    added = 0
    for page, rect in add or []:
        if 0 <= page < len(analysis.pages):
            # drawn on the rendered page: back to page space
            snapped = analysis.snap(page, from_display(rect, analysis.pages[page]))
            boxes.append(
                Box(page, snapped, "PERSON", "", 1.0, ["manual-area"], "manual", True)
            )
            added += 1
    found = analysis.search(search) if search else []
    boxes += found
    return EditOutcome(added=added, search_hits=len(found))


def display_boxes(analysis: Analysis) -> list[dict[str, Any]]:
    """Every box with its id, as shown on the rendered pages.

    Page space differs from display space on rotated pages.
    """
    return [
        {"id": i, **asdict(b), "rect": to_display(b.rect, analysis.pages[b.page])}
        for i, b in enumerate(analysis.boxes)
    ]


@pdfium_locked
def render_png(src: Path, page: int, dpi: int, dest: Path) -> None:
    """Render one page of ``src`` to ``dest`` as PNG.

    Raises:
        PageNotFoundError: ``page`` is outside the document.
    """
    doc = pdfium.PdfDocument(src)
    try:
        if not 0 <= page < len(doc):
            raise PageNotFoundError()
        doc[page].render(scale=dpi / 72).to_pil().save(dest, "PNG")
    finally:
        doc.close()
