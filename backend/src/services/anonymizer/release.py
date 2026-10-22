"""Redaction and release: pypdfium2 renders, pypdf rewrites.

Every page with a box is replaced by a rendering of itself with the boxes
painted in: nothing of its content survives but pixels (no glyph, font subset,
image or hidden object under a box). An invisible text layer with the words
that are not redacted keeps the page searchable. Pages without boxes are copied
as they are. pypdf then rebuilds the file from the pages alone: no metadata,
XMP, bookmarks, structure tree, names, forms, attachments, JavaScript,
annotations or incremental history.

The checks are those of ``verify.py`` (nothing left under the boxes, no value
anywhere in the file, pixels unchanged outside the boxes). A page that fails
them is flattened too, then the document is checked again; it is released only
if every check passes.
"""

import ctypes
import dataclasses
import io
import logging
import re
import time
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter

from src.services.anonymizer.geometry import (
    area,
    from_display,
    overlap_area,
)
from src.services.anonymizer.layers import Rect, TextLayer, Token
from src.services.anonymizer.pdf_pages import (
    FLATTEN_DPI,
    page_box,
    pdfium_locked,
    render_page,
)
from src.services.anonymizer.pipeline import (
    Analysis,
    Box,
    to_display,
)
from src.services.anonymizer.taxonomy import LABELS
from src.services.anonymizer.verify import (
    Release,
    strong_values,
    verify,
)

logger = logging.getLogger(__name__)

PAD = 1.0  # points added around every box before painting it
PRODUCER = "anonymization-pipeline"
# page entries that can carry text outside the page content
PAGE_KEYS = (
    "/Annots",
    "/PieceInfo",
    "/Metadata",
    "/Thumb",
    "/AA",
    "/StructParents",
    "/B",
)

_STYLES = {
    # fill, border, text colour (RGB 0-255)
    "black": ((0, 0, 0), None, None),
    "label": ((222, 235, 252), (140, 173, 230), (28, 64, 140)),
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def _painted(
    analysis: Analysis, pno: int, width: float, height: float
) -> list[tuple[Rect, str]]:
    """Every box to redact on a page, padded and clipped to it (page space), with its label."""
    out = []
    for b in analysis.boxes:
        if b.page != pno or not b.redact:
            continue
        x0, y0, x1, y1 = b.rect
        r = (
            max(0.0, x0 - PAD),
            max(0.0, y0 - PAD),
            min(width, x1 + PAD),
            min(height, y1 + PAD),
        )
        if r[2] > r[0] and r[3] > r[1]:
            out.append((r, b.label))
    return out


def _paint(
    img: Image.Image, rects: list[tuple[Rect, str]], scale: float, style: str
) -> None:
    fill, border, colour = _STYLES[style]
    draw = ImageDraw.Draw(img)
    for (x0, y0, x1, y1), label in rects:
        box = (x0 * scale, y0 * scale, x1 * scale, y1 * scale)
        draw.rectangle(box, fill=fill, outline=border)
        if colour is None:
            continue
        lab = LABELS[label]
        size = min((y1 - y0) * 0.72, 9.0) * scale
        for tag in (lab.tag, lab.short):
            font = ImageFont.load_default(size=max(size, 1.0))
            if draw.textlength(tag, font=font) <= box[2] - box[0] - 2 * scale:
                draw.text(
                    ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2),
                    tag,
                    fill=colour,
                    font=font,
                    anchor="mm",
                )
                break


def _text_tokens(analysis: Analysis, pno: int) -> list[Token]:
    """Words of a page for the invisible text layer: the OCR layer of scans, else native."""
    info = analysis.pages[pno]
    layers: list[TextLayer] = [L for L in analysis.layers if L.page == pno]
    layer = None
    if info.get("scanned"):
        layer = next((L for L in layers if L.source == "ocr"), None)
    layer = layer or next((L for L in layers if L.source == "native"), None)
    return layer.tokens if layer else []


def _overlaps(a: Rect, b: Rect) -> bool:
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _set_text(obj: ctypes.c_void_p, text: str) -> float:
    """Set the text of a text object; returns its width before any transform."""
    encoded = (text + "\x00").encode("utf-16-le")
    buf = ctypes.create_string_buffer(encoded, len(encoded))
    pdfium_c.FPDFText_SetText(obj, ctypes.cast(buf, ctypes.POINTER(ctypes.c_ushort)))
    left, bottom, right, top = (ctypes.c_float() for _ in range(4))
    pdfium_c.FPDFPageObj_GetBounds(
        obj,
        ctypes.byref(left),
        ctypes.byref(bottom),
        ctypes.byref(right),
        ctypes.byref(top),
    )
    return right.value - left.value


def _add_invisible_text(
    pdf: pdfium.PdfDocument,
    page: pdfium.PdfPage,
    tokens: list[Token],
    painted: list[Rect],
    width: float,
    height: float,
) -> int:
    """Invisible words over a flattened page, so it stays searchable. Words that touch a
    painted box, lie outside the page or run vertically are left out."""
    font = pdfium_c.FPDFText_LoadStandardFont(pdf.raw, b"Helvetica")
    added = 0
    for i, t in enumerate(tokens):
        x0, y0, x1, y1 = t.rect
        if x0 < 0 or y0 < 0 or x1 > width or y1 > height or x1 <= x0 or y1 <= y0:
            continue
        if (y1 - y0) > 1.5 * (x1 - x0) and len(t.text) > 2:
            continue  # vertical: no reliable baseline for a horizontal text object
        if any(_overlaps(t.rect, r) for r in painted):
            continue
        size = (y1 - y0) * 0.8
        obj = pdfium_c.FPDFPageObj_CreateTextObj(pdf.raw, font, ctypes.c_float(size))
        w = _set_text(obj, t.text)
        sx = (x1 - x0) / w if w > 0 else 1.0
        # a word followed by another on its line ends with a space: text extraction
        # would not always infer it from the gap (the scale keeps the word's width)
        follows = i + 1 < len(tokens) and tokens[i + 1].line == t.line
        if follows:
            _set_text(obj, t.text + " ")
        pdfium_c.FPDFTextObj_SetTextRenderMode(
            obj, pdfium_c.FPDF_TEXTRENDERMODE_INVISIBLE
        )
        # baseline a fifth of the height above the bottom of the word box
        pdfium_c.FPDFPageObj_Transform(
            obj, sx, 0, 0, 1, x0, height - y1 + (y1 - y0) * 0.2
        )
        pdfium_c.FPDFPage_InsertObject(page.raw, obj)
        added += 1
    return added


def _flat_page(
    out: pdfium.PdfDocument,
    src_page: pdfium.PdfPage,
    analysis: Analysis,
    pno: int,
    style: str,
) -> list[tuple[int, Rect, str]]:
    """Append to ``out`` the rendering of ``src_page`` with its boxes painted in.

    The page is rendered as it is displayed and stored upright at the displayed
    size: an image drawn rotated would be resampled and fail the pixel check.
    """
    info = analysis.pages[pno]
    cx0, cy0, cx1, cy1 = page_box(src_page)
    pw, ph = cx1 - cx0, cy1 - cy0
    rotated = src_page.get_rotation() % 180 == 90
    width, height = (ph, pw) if rotated else (pw, ph)
    scale = FLATTEN_DPI / 72
    img = Image.fromarray(render_page(src_page, scale))
    painted = _painted(analysis, pno, pw, ph)
    shown = [(to_display(r, info), label) for r, label in painted]
    _paint(img, shown, scale, style)
    page = out.new_page(width, height)
    image = pdfium.PdfImage.new(out)
    # lossless: the pixel check compares it with the original rendering
    image.set_bitmap(pdfium.PdfBitmap.from_pil(img))
    image.set_matrix(pdfium.PdfMatrix().scale(width, height))
    page.insert_obj(image)
    tokens = [
        dataclasses.replace(t, rect=to_display(t.rect, info))
        for t in _text_tokens(analysis, pno)
    ]
    _add_invisible_text(out, page, tokens, [r for r, _ in shown], width, height)
    page.gen_content()
    return [(pno, r, label) for r, label in painted]


def cover_values(analysis: Analysis) -> int:
    """Box every occurrence of a redacted value that no box covers yet.

    Identifiers and full names redacted in one place may be missed elsewhere
    (glued to a label, broken by OCR, in a fax header). The output check finds
    them, ignoring whitespace; on a scan they would stay visible in the pixels.
    The same matching is used here, so they are painted over before the check.
    Occurrences the user chose to keep stay as they are.

    Returns:
        The number of boxes added (source ``propagated``).
    """
    labels: dict[str, str] = {}
    for b in analysis.boxes:
        if b.redact:
            labels.setdefault(b.text, b.label)
    kept = [
        b for b in analysis.boxes if not b.redact and "removed-by-user" in b.sources
    ]
    added = 0
    for value in sorted(strong_values(analysis)):
        chars = _norm(value)
        if not chars:
            continue
        rx = re.compile(r"\s*".join(re.escape(c) for c in chars), re.IGNORECASE)
        for layer in analysis.layers:
            for m in rx.finditer(layer.text):
                for r in layer.rects(m.start(), m.end()):

                    def covers(x: Box, r: Rect = r, page: int = layer.page) -> bool:
                        return x.page == page and overlap_area(x.rect, r) >= 0.5 * area(
                            r
                        )

                    if any(x.redact and covers(x) for x in analysis.boxes):
                        continue
                    if any(covers(x) for x in kept):
                        continue
                    analysis.boxes.append(
                        Box(
                            layer.page,
                            r,
                            labels.get(value, "PERSON"),
                            m.group(),
                            1.0,
                            ["propagated"],
                            layer.source,
                            True,
                        )
                    )
                    added += 1
    return added


def rebuild(
    src: Path, analysis: Analysis, out: Path, pages: set[int], style: str
) -> None:
    """Write ``out``: ``src`` with ``pages`` flattened, then rewritten by ``scrub``.

    The painted rectangles are stored in ``analysis.applied`` for the pixel check.
    """
    applied: list[tuple[int, Rect, str]] = []
    original = pdfium.PdfDocument(src)
    built = pdfium.PdfDocument.new()
    try:
        for pno in range(len(original)):
            if pno in pages:
                applied += _flat_page(built, original[pno], analysis, pno, style)
            else:
                built.import_pages(original, [pno])
        buf = io.BytesIO()
        built.save(buf)
    finally:
        built.close()
        original.close()
    scrub(buf.getvalue(), out)
    analysis.applied = applied


def scrub(data: bytes, out: Path) -> None:
    """Rebuild a PDF from its pages alone: a fresh catalog, no metadata, no page extras."""
    reader = PdfReader(io.BytesIO(data))
    writer = PdfWriter()
    for src_page in reader.pages:
        page = writer.add_page(src_page)
        for key in PAGE_KEYS:
            if key in page:
                del page[key]
    writer.add_metadata({"/Producer": PRODUCER})
    writer.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)
    with out.open("wb") as fh:
        writer.write(fh)


def _page_rect(page: pdfium.PdfPage, bounds: tuple[float, float, float, float]) -> Rect:
    """PDF-space bounds on an output page as a top-left rectangle of that page."""
    cx0, _, _, cy1 = page_box(page)
    left, bottom, right, top = bounds
    return (left - cx0, cy1 - top, right - cx0, cy1 - bottom)


def cover_found(out: Path, analysis: Analysis, flat: set[int]) -> int:
    """Box the words of ``out`` that still spell a redacted value, read as the check reads them.

    The output text is read in PDFium's order, which can join pieces the text
    layers keep apart (a URL wrapped next to a QR code). Every occurrence of a
    value is found in that text, ignoring whitespace, and the text objects of
    its characters become boxes (page space; flattened pages are stored upright,
    as displayed).

    Returns:
        The number of boxes added (source ``propagated``).
    """
    values = {_norm(v): v for v in strong_values(analysis)}
    labels: dict[str, str] = {}
    for b in analysis.boxes:
        if b.redact:
            labels.setdefault(b.text, b.label)
    added = 0
    with pdfium.PdfDocument(out) as doc:
        for pno in range(len(doc)):
            page = doc[pno]
            textpage = page.get_textpage()
            chars, index = [], []
            for i in range(textpage.count_chars()):
                c = textpage.get_text_range(i, 1)
                if c and not c.isspace():
                    chars.append(c.lower())
                    index.append(i)
            text = "".join(chars)
            for norm, value in values.items():
                start = text.find(norm)
                while start >= 0:
                    seen: set[tuple[float, float, float, float]] = set()
                    for i in index[start : start + len(norm)]:
                        obj = textpage.get_textobj(i)
                        if obj is None:
                            continue
                        bounds = tuple(obj.get_bounds())
                        if bounds in seen:
                            continue
                        seen.add(bounds)  # type: ignore[arg-type]
                        rect = _page_rect(page, bounds)  # type: ignore[arg-type]
                        if pno in flat:
                            rect = from_display(rect, analysis.pages[pno])
                        analysis.boxes.append(
                            Box(
                                pno,
                                rect,
                                labels.get(value, "PERSON"),
                                value,
                                1.0,
                                ["propagated"],
                                "native",
                                True,
                            )
                        )
                        added += 1
                    start = text.find(norm, start + 1)
    return added


def _pages_with_values(out: Path, analysis: Analysis) -> set[int]:
    values = [_norm(v) for v in strong_values(analysis)]
    found = set()
    with pdfium.PdfDocument(out) as doc:
        for pno in range(len(doc)):
            text = _norm(doc[pno].get_textpage().get_text_bounded())
            if any(v and v in text for v in values):
                found.add(pno)
    return found


@pdfium_locked
def redact_and_verify(
    src: Path,
    analysis: Analysis,
    out: Path,
    style: str = "black",
    tol: float = 2e-4,
) -> Release:
    """Flatten the pages with boxes, verify, flatten the pages that fail, verify again.

    Returns:
        The final verification report, the flattened pages and the time spent.
    """
    t0 = time.perf_counter()
    covered = cover_values(analysis)
    if covered:
        logger.info(
            "Redacted values covered where detection missed them: boxes=%d", covered
        )
    pages = {b.page for b in analysis.boxes if b.redact}
    rebuild(src, analysis, out, pages, style)
    t1 = time.perf_counter()
    rep = verify(src, out, analysis, pages, tol=tol)
    t2 = time.perf_counter()
    seconds = {"redaction": t1 - t0, "verification": t2 - t1}
    if not rep.ok:
        # values the boxes missed, found where the check found them, then pages
        # that failed another check: flattened, and everything checked again
        found = cover_found(out, analysis, pages) if rep.whole_file else 0
        more = set(rep.pages) | {b.page for b in analysis.boxes if b.redact}
        if rep.whole_file and not found:
            more |= _pages_with_values(out, analysis)
        if found or more - pages:
            flattened = len(more - pages)
            pages |= more
            rebuild(src, analysis, out, pages, style)
            t3 = time.perf_counter()
            rep = verify(src, out, analysis, pages, tol=tol)
            seconds["redaction"] += t3 - t2
            seconds["verification"] += time.perf_counter() - t3
            logger.info(
                "Second pass after a failed check: values=%d pages=%d verified=%s",
                found,
                flattened,
                rep.ok,
            )
    return Release(rep, sorted(pages), {k: round(v, 2) for k, v in seconds.items()})
