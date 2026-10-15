"""Fail-closed verification of a redacted PDF.

1. structure: one revision only, none of the document parts that can carry personal data
   (metadata, XMP, scripts, attachments, forms, outlines, structure tree), no annotations;
2. text under the boxes: no character of the output lies under a box of a text value;
3. values: no strong value (identifiers, full names) is in the text of the output (invisible text
   included), in its decoded streams and strings, in its raw bytes or in its metadata;
4. pixels: outside the boxes of each page the rendering is the same as the original;
5. re-detection: the text detectors on the output find no redacted value.

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
from anonymization_pipeline.layers import TextLayer
from anonymization_pipeline.layers.native import native_layer
from anonymization_pipeline.pdf import PDFIUM_LOCK
from anonymization_pipeline.pdf.geometry import center, contains_point, grow
from anonymization_pipeline.pipeline_config import PipelineConfig, registry
from anonymization_pipeline.render.boxes import Box
from anonymization_pipeline.render.pdf import Redacted

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
        for token in layers[box.page].tokens:
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


def _redetection(
    layers: list[TextLayer],
    strong: dict[str, str],
    cfg: PipelineConfig,
    report: VerificationReport,
) -> None:
    for f in detect(layers, cfg).found:
        value = _norm(layers[f.layer].text[f.start : f.end])
        if value in strong:
            report.fail("value_detected_again", layers[f.layer].page, f.category)


def verify(
    original: bytes,
    redacted: Redacted,
    values: set[tuple[str, str]],
    cfg: PipelineConfig,
) -> VerificationReport:
    """Run every check on the redacted PDF.

    Args:
        original: The PDF before redaction.
        redacted: The result of ``render.pdf.redact``.
        values: ``(text, category)`` that were redacted (IN CLEAR TEXT: never log them).
        cfg: Configuration of the run.
    """
    report = VerificationReport()
    strong = strong_values(values)
    reader = _structure(redacted.pdf, report)
    layers = _layers(redacted.pdf)
    _under_boxes(layers, redacted.boxes, report)
    _values(redacted.pdf, reader, layers, strong, report)
    _pixels(original, redacted, cfg, report)
    _redetection(layers, strong, cfg, report)
    return report
