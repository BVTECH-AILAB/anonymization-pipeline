"""Fail-closed verification of a redacted PDF.

1. structure: one revision only, none of the document parts that can carry personal data
   (metadata, XMP, scripts, attachments, forms, outlines, structure tree), no annotations;
2. text under the boxes: no character of the output lies under a box of a text value;
3. values: no strong value (identifiers, full names) is in the text of the output (invisible text
   included), in its decoded streams and strings, in its raw bytes or in its metadata;
4. pixels: outside the boxes of each page the rendering is the same as the original;
5. x-ray (surgical pages): rendered without the covers, the boxes no longer show what the
   original showed there (text, images or vectors left under a cover);
6. re-detection: the text detectors on the output find no redacted value.

The report names checks, pages and categories, never values. Any failure blocks the download.
"""

import io
import re
from dataclasses import dataclass, field
from typing import cast

import numpy as np
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from pypdf import PdfReader
from pypdf.generic import (
    ArrayObject,
    ByteStringObject,
    DictionaryObject,
    IndirectObject,
    StreamObject,
    TextStringObject,
)

from anonymization_pipeline.detection import detect
from anonymization_pipeline.detectors.ner import Engine
from anonymization_pipeline.layers import TextLayer
from anonymization_pipeline.layers.native import native_layer
from anonymization_pipeline.pdf import PDFIUM_LOCK
from anonymization_pipeline.pdf.geometry import center, contains_point, grow
from anonymization_pipeline.pipeline_config import PipelineConfig, registry
from anonymization_pipeline.render.boxes import Box
from anonymization_pipeline.render.pdf import Redacted
from anonymization_pipeline.render.surgical import is_cover

# catalog keys that must not survive the scrub
FORBIDDEN_ROOT = (
    "/Metadata",
    "/OpenAction",
    "/AA",
    "/AcroForm",
    "/Names",
    "/Outlines",
    "/StructTreeRoot",
    "/MarkInfo",
    "/Perms",
    "/SpiderInfo",
)
FORBIDDEN_PAGE = ("/Annots", "/Thumb", "/PieceInfo", "/Metadata", "/AA")
# families whose values are too common to be searched alone (a place, a date)
WEAK_FAMILIES = {"LUOGO", "DATA", "ORGANIZZAZIONE"}
MIN_STRONG = 6  # characters without spaces of a strong identifier
_IMAGE_FILTERS = {"/DCTDecode", "/JPXDecode", "/JBIG2Decode", "/CCITTFaxDecode"}


@dataclass(frozen=True)
class Failure:
    """A failed check: never the value."""

    check: str
    page: int | None = None  # zero-based
    category: str | None = None


@dataclass
class VerificationReport:
    failures: list[Failure] = field(default_factory=list)
    changed_outside: dict[int, float] = field(
        default_factory=dict
    )  # page -> pixel fraction

    @property
    def ok(self) -> bool:
        return not self.failures

    def fail(
        self, check: str, page: int | None = None, category: str | None = None
    ) -> None:
        failure = Failure(check, page, category)
        if failure not in self.failures:
            self.failures.append(failure)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def strong_values(values: set[tuple[str, str]]) -> dict[str, str]:
    """Values searched in the whole output (normalized value -> category).

    Identifiers of at least ``MIN_STRONG`` characters and full names of persons; single surnames,
    places and dates are too common (an eponym, a hospital in the same city) to be searched alone.
    """
    reg = registry()
    out: dict[str, str] = {}
    for value, category in values:
        family = reg.family(category)
        if family == "PERSONA":
            if len(value.split()) >= 2:
                out[_norm(value)] = category
        elif family not in WEAK_FAMILIES and len(_norm(value)) >= MIN_STRONG:
            out[_norm(value)] = category
    return out


def _layers(data: bytes) -> list[TextLayer]:
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(data)
        try:
            return [native_layer(pdf[i], i) for i in range(len(pdf))]
        finally:
            pdf.close()


def _structure(data: bytes, report: VerificationReport) -> PdfReader:
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(data)
        try:
            revisions = pdfium_c.FPDF_GetTrailerEnds(pdf.raw, None, 0)
        finally:
            pdf.close()
    if revisions != 1:
        report.fail("incremental_revisions")
    reader = PdfReader(io.BytesIO(data))
    root = cast(DictionaryObject, reader.trailer["/Root"].get_object())
    if any(key in root for key in FORBIDDEN_ROOT):
        report.fail("document_parts")
    info = reader.trailer.get("/Info")
    if info is not None and set(info.get_object()) - {"/Producer"}:
        report.fail("metadata")
    for i, page in enumerate(reader.pages):
        if any(key in page for key in FORBIDDEN_PAGE):
            report.fail("page_parts", i)
    return reader


def _strings(obj: object, out: list[bytes], seen: set[int]) -> None:
    """Every string and decoded non-image stream reachable from ``obj``."""
    if isinstance(obj, IndirectObject):
        if obj.idnum in seen:
            return
        seen.add(obj.idnum)
        obj = obj.get_object()
    if isinstance(obj, TextStringObject):
        out.append(
            obj.encode("utf-16-be", "ignore") + str(obj).encode("latin-1", "ignore")
        )
    elif isinstance(obj, ByteStringObject):
        out.append(bytes(obj))
    elif isinstance(obj, StreamObject):
        filters = obj.get("/Filter")
        names = set(filters) if isinstance(filters, ArrayObject) else {filters}
        if obj.get("/Subtype") != "/Image" and not names & _IMAGE_FILTERS:
            try:
                out.append(obj.get_data())
            except Exception:  # noqa: BLE001 - an undecodable stream is searched raw
                out.append(obj._data)
        for value in obj.values():
            _strings(value, out, seen)
    elif isinstance(obj, DictionaryObject):
        for key, value in obj.items():
            if key != "/Parent":
                _strings(value, out, seen)
    elif isinstance(obj, ArrayObject):
        for value in obj:
            _strings(value, out, seen)


def _encodings(value: str) -> list[bytes]:
    return [
        value.encode("latin-1", "ignore").lower(),
        value.encode("utf-16-be").lower(),
    ]


def _values(
    data: bytes,
    reader: PdfReader,
    layers: list[TextLayer],
    strong: dict[str, str],
    report: VerificationReport,
) -> None:
    texts = [_norm(layer.text) for layer in layers]
    blobs: list[bytes] = [data]
    _strings(reader.trailer, blobs, set())
    haystack = b"\0".join(blobs).lower()
    compact = re.sub(rb"\s+", b"", haystack)
    for value, category in strong.items():
        for i, text in enumerate(texts):
            if value in text:
                report.fail("value_in_text", i, category)
        if any(v in haystack or v in compact for v in _encodings(value)):
            report.fail("value_in_file", None, category)


def _under_boxes(
    layers: list[TextLayer], boxes: list[Box], report: VerificationReport
) -> None:
    for box in boxes:
        if box.source == "visual":
            continue
        x0, y0, x1, y1 = box.rect
        inset = grow(box.rect, -1.0, -(y1 - y0) * 0.3)
        tag = set(
            box.category.split("_")
        )  # the label style writes the category on the cover
        for token in layers[box.page].tokens:
            if token.text in tag:
                continue
            if any(contains_point(inset, *center(c)) for c in token.chars):
                report.fail("text_under_box", box.page, box.category)
                break


def _render(data: bytes, page: int, scale: float) -> np.ndarray:
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(data)
        try:
            bitmap = pdf[page].render(
                scale=scale, draw_annots=False, may_draw_forms=False
            )
            image = np.asarray(bitmap.to_pil().convert("RGB"), dtype=np.int16)
            bitmap.close()
            return image
        finally:
            pdf.close()


def _page_count(data: bytes) -> int:
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(data)
        try:
            return len(pdf)
        finally:
            pdf.close()


def _pixels(
    original: bytes,
    redacted: Redacted,
    cfg: PipelineConfig,
    report: VerificationReport,
) -> None:
    """Rendering outside the boxes, page by page, against the original.

    A rasterised page is compared at the resolution of its raster, where the two renderings are
    the same pixel for pixel outside the boxes; the other pages at ``pixel_dpi``.
    """
    v = cfg.verification
    pages = _page_count(original)
    if _page_count(redacted.pdf) != pages:
        report.fail("page_count")
        return
    for page in range(pages):
        flattened = redacted.strategies.get(page) == "flatten"
        dpi = cfg.redaction.flatten_dpi if flattened else v.pixel_dpi
        scale = dpi / 72
        a, b = _render(original, page, scale), _render(redacted.pdf, page, scale)
        h, w = min(a.shape[0], b.shape[0]), min(a.shape[1], b.shape[1])
        changed = np.abs(a[:h, :w] - b[:h, :w]).max(axis=2) > v.pixel_threshold
        for box in redacted.boxes:
            if box.page != page:
                continue
            x0, y0, x1, y1 = box.rect
            changed[
                max(0, int((y0 - 3) * scale)) : int((y1 + 3) * scale) + 1,
                max(0, int((x0 - 3) * scale)) : int((x1 + 3) * scale) + 1,
            ] = False
        fraction = float(changed.mean())
        report.changed_outside[page] = fraction
        if fraction > v.pixel_tolerance:
            report.fail("pixels_outside_boxes", page)


XRAY_CONTENT = (
    60  # channel distance from the background of a box that makes a pixel content
)
XRAY_CHANGED = 40  # channel distance that counts as removed content
XRAY_MAX = (
    0.05  # fraction of the original content of a box that may show without the cover
)
XRAY_DPI = 144
XRAY_GLYPH_PAD = 0.5  # points around a kept glyph (anti-aliasing)
XRAY_SIDE = (
    1.5  # points left out at the sides of a box: glyphs of the next words overhang
)


_CONTENT_TYPES = (
    pdfium_c.FPDF_PAGEOBJ_TEXT,
    pdfium_c.FPDF_PAGEOBJ_IMAGE,
    pdfium_c.FPDF_PAGEOBJ_FORM,
)


def _render_content(data: bytes, page: int, scale: float, covers: bool) -> np.ndarray:
    """The page with only its text, images and forms (vector paths and shadings hidden), without
    the covers of the surgical redaction unless ``covers``."""
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(data)
        try:
            p = pdf[page]
            for obj in p.get_objects(max_depth=1):
                if obj.type not in _CONTENT_TYPES or (not covers and is_cover(obj.raw)):
                    pdfium_c.FPDFPageObj_SetIsActive(obj.raw, 0)
            bitmap = p.render(scale=scale, draw_annots=False, may_draw_forms=False)
            image = np.asarray(bitmap.to_pil().convert("RGB"), dtype=np.int16)
            bitmap.close()
            return image
        finally:
            pdf.close()


def _kept_glyphs(
    layer: TextLayer, box: Box, scale: float, shape: tuple[int, int]
) -> np.ndarray:
    """Pixels of the glyphs the redactor kept on purpose near a box: characters of the output
    whose centre is outside the box (a tilted neighbouring line, a line touched by a visual box).
    A character centred inside the box is never excused."""
    mask = np.zeros(shape, dtype=bool)
    for token in layer.tokens:
        for rect in token.chars:
            if contains_point(box.rect, *center(rect)):
                continue
            x0, y0, x1, y1 = grow(rect, XRAY_GLYPH_PAD, XRAY_GLYPH_PAD)
            mask[
                max(0, int(y0 * scale)) : max(0, int(y1 * scale) + 1),
                max(0, int(x0 * scale)) : max(0, int(x1 * scale) + 1),
            ] = True
    return mask


def _xray(
    original: bytes,
    redacted: Redacted,
    layers: list[TextLayer],
    report: VerificationReport,
) -> None:
    """Surgical pages without their covers: the boxes must no longer show the text and images
    the original showed there.

    Both pages are rendered with only their text, images and forms: vector frames, rules and
    bands partly under a box stay on purpose (they are layout). Content is what differs from the
    background of the box, taken from the output (in a tight box around bold text the original is
    half ink); it must have changed once the cover is taken away. When nothing was removed, the
    output is the original and the content shows up unchanged. The ink of the characters kept
    beside the box (centred outside it) is not content under the box.
    """
    scale = XRAY_DPI / 72
    for page in sorted(p for p, s in redacted.strategies.items() if s == "surgical"):
        before = _render_content(original, page, scale, covers=True)
        after = _render_content(redacted.pdf, page, scale, covers=False)
        h, w = (
            min(before.shape[0], after.shape[0]),
            min(before.shape[1], after.shape[1]),
        )
        for box in redacted.boxes:
            if box.page != page:
                continue
            kept = _kept_glyphs(layers[page], box, scale, (h, w))
            x0, y0, x1, y1 = box.rect
            region = (
                slice(max(0, int(y0 * scale)), min(h, int(y1 * scale) + 1)),
                slice(
                    max(0, int((x0 + XRAY_SIDE) * scale)),
                    min(w, int((x1 - XRAY_SIDE) * scale) + 1),
                ),
            )
            a, b = before[region], after[region]
            if a.size == 0:
                continue
            background = np.median(b.reshape(-1, 3), axis=0)
            content = np.abs(a - background).max(axis=2) > XRAY_CONTENT
            unchanged = np.abs(a - b).max(axis=2) <= XRAY_CHANGED
            left = content & unchanged & ~kept[region]
            if content.sum() >= 4 and left.sum() > XRAY_MAX * content.sum():
                report.fail("content_under_box", page, box.category)


def _redetection(
    layers: list[TextLayer],
    strong: dict[str, str],
    cfg: PipelineConfig,
    report: VerificationReport,
    ner: Engine | None,
) -> None:
    for f in detect(layers, cfg, ner).found:
        value = _norm(layers[f.layer].text[f.start : f.end])
        if value in strong:
            report.fail("value_detected_again", layers[f.layer].page, f.category)


def verify(
    original: bytes,
    redacted: Redacted,
    values: set[tuple[str, str]],
    cfg: PipelineConfig,
    ner: Engine | None = None,
) -> VerificationReport:
    """Run every check on the redacted PDF.

    Args:
        original: The PDF before redaction.
        redacted: The result of ``render.pdf.redact``.
        values: ``(text, category)`` that were redacted (IN CLEAR TEXT: never log them).
        cfg: Configuration of the run.
        ner: The NER model loaded at startup, when NER is enabled.
    """
    report = VerificationReport()
    strong = strong_values(values)
    reader = _structure(redacted.pdf, report)
    layers = _layers(redacted.pdf)
    _under_boxes(layers, redacted.boxes, report)
    _values(redacted.pdf, reader, layers, strong, report)
    _pixels(original, redacted, cfg, report)
    _xray(original, redacted, layers, report)
    _redetection(layers, strong, cfg, report, ner)
    return report
