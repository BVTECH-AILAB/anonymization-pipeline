"""Surgical redaction with PyMuPDF.

Under each box: text glyphs are deleted from the content stream, image pixels
are blanked, vector art fully inside the box is removed. Everything outside the
boxes is left untouched. Then document-level scrub and a full clean rewrite
(drops incremental-save history).
"""

import dataclasses
from pathlib import Path

import pymupdf

from anonmyzation_pipeline.services.anonymization.layers import Rect
from anonmyzation_pipeline.services.anonymization.pipeline import Analysis, Box
from anonmyzation_pipeline.services.anonymization.taxonomy import LABELS

STYLES = {
    # fill, border, text colour
    "label": ((0.87, 0.92, 0.99), (0.55, 0.68, 0.9), (0.11, 0.25, 0.55)),
    "black": ((0, 0, 0), None, None),  # default: solid black box, no tag
}


def _tag(label: str, rect: pymupdf.Rect) -> tuple[str, float]:
    """Largest readable tag that fits the box (the PDF cannot reflow)."""
    lab = LABELS[label]
    for tag in (lab.tag, lab.short):
        w1 = pymupdf.get_text_length(tag, "helv", 1)
        fs = min(rect.height * 0.72, (rect.width - 2) / w1, 9)
        if fs >= 4.5 or tag == lab.short:
            return (tag, fs) if fs >= 3 else ("", 0)
    return "", 0


# pymupdf.PDF_REDACT_* values (the type stubs do not expose them; a test pins them).
REDACT_IMAGE_PIXELS = 2  # blank only the pixels under the box
REDACT_LINE_ART_IF_COVERED = 1  # remove vector art fully inside the box
REDACT_TEXT_REMOVE = 0  # remove the glyphs under the box


@dataclasses.dataclass
class _Item:
    rect: pymupdf.Rect
    label: str
    layer: str


def _layout(boxes: list[Box]) -> list[tuple[pymupdf.Rect, str, bool, str]]:
    """Merge heavily overlapping boxes and silence tags inside visual boxes,
    so labels never print on top of each other.

    Returns:
        ``(rect, label, show_tag, layer)`` per box to draw.
    """
    items = [_Item(pymupdf.Rect(b.rect), b.label, b.layer) for b in boxes]
    merged = True
    while merged:
        merged = False
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i].rect, items[j].rect
                inter = (a & b).get_area()
                if inter and inter > 0.4 * min(a.get_area(), b.get_area()):
                    keep = (
                        items[i]
                        if (items[i].layer == "visual") >= (items[j].layer == "visual")
                        else items[j]
                    )
                    items[i] = _Item(a | b, keep.label, keep.layer)
                    items.pop(j)
                    merged = True
                    break
            if merged:
                break
    out = []
    for item in items:
        inside_visual = item.layer != "visual" and any(
            other.layer == "visual"
            and (item.rect & other.rect).get_area() > 0.5 * item.rect.get_area()
            for other in items
        )
        out.append((item.rect, item.label, not inside_visual, item.layer))
    return out


def redact(
    src: Path, analysis: Analysis, out: Path, style: str = "black"
) -> dict[str, int]:
    """Write ``out``: ``src`` with every redacted box removed and covered.

    Args:
        src: Original PDF.
        analysis: Boxes to redact (``redact=True``).
        out: Destination PDF.
        style: ``black`` (solid box) or ``label`` (light box with the PII type).

    Returns:
        ``{"boxes": n}``, the number of rectangles applied. The rectangles are
        also stored in ``analysis.applied`` for the verification.
    """
    fill, border, txt = STYLES[style]
    doc = pymupdf.open(src)
    applied: list[tuple[int, Rect, str]] = []
    for pno in range(doc.page_count):
        page = doc[pno]
        page_boxes = []
        for b in analysis.boxes:
            if b.page != pno or not b.redact:
                continue
            r = pymupdf.Rect(b.rect)
            if b.layer == "native":
                # char boxes include line-gap; inset vertically so neighbours are not clipped
                inset = r.height * 0.12
                r = pymupdf.Rect(r.x0 - 0.3, r.y0 + inset, r.x1 + 0.3, r.y1 - inset)
            elif b.layer in ("ocr", "manual"):
                r = r + (-1.5, -1.5, 1.5, 1.5)
            r &= page.rect
            if not r.is_empty:
                b2 = dataclasses.replace(b, rect=(r.x0, r.y0, r.x1, r.y1))
                page_boxes.append(b2)
        for r, label, show, _layer in _layout(page_boxes):
            tag, fs = _tag(label, r) if show and txt is not None else ("", 0)
            page.add_redact_annot(
                r,
                text=tag or None,
                fontname="helv",
                fontsize=fs or 4,
                align=pymupdf.TEXT_ALIGN_CENTER,
                fill=fill,
                text_color=txt or (0, 0, 0),
                cross_out=False,
            )
            applied.append((pno, (r.x0, r.y0, r.x1, r.y1), label))
        if page_boxes:
            page.apply_redactions(
                images=REDACT_IMAGE_PIXELS,
                graphics=REDACT_LINE_ART_IF_COVERED,
                text=REDACT_TEXT_REMOVE,
            )
            if border:
                for apno, rect, _ in applied:
                    if apno == pno:
                        page.draw_rect(rect, color=border, width=0.4)
        # annotations can carry PII (notes, form values): remove them all
        for annot in list(page.annots() or []):
            page.delete_annot(annot)
        for w in list(page.widgets() or []):
            page.delete_widget(w)
    doc.scrub(
        attached_files=True,
        clean_pages=False,
        embedded_files=True,
        hidden_text=False,
        javascript=True,
        metadata=True,
        redactions=False,
        remove_links=True,
        reset_fields=True,
        reset_responses=True,
        thumbnails=True,
        xml_metadata=True,
    )
    doc.set_metadata({"producer": "anonmyzation-pipeline"})
    doc.save(
        out, garbage=4, deflate=True, clean=True
    )  # full rewrite: no incremental history
    doc.close()
    analysis.applied = (
        applied  # what verification must exclude from the pixel comparison
    )
    return {"boxes": len(applied)}
