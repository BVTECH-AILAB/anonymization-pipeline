"""Anonymization of one document: ingest -> text layers -> detection -> redaction -> verification.

CPU-bound and synchronous: the job worker runs it in a thread. Pages are read one at a time: the
native text layer, whether the page is a scan, a rendering for the visual detectors and, for a
scan, the OCR words (the rendering under ``PDFIUM_LOCK``, the inference outside it). A document is
released only when every check of the verification passed; otherwise it is blocked and no
redacted file is kept. A document with scanned pages is blocked when OCR is off: its text would
never be read, so nothing on those pages would be redacted.
"""

import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from anonymization_pipeline import ocr as ocr_module
from anonymization_pipeline.detection import detect, visual
from anonymization_pipeline.detectors.base import DetectorUnavailable
from anonymization_pipeline.detectors.ner import Engine
from anonymization_pipeline.ingest import ingest
from anonymization_pipeline.layers import TextLayer
from anonymization_pipeline.layers.native import native_layer
from anonymization_pipeline.layers.ocr import ocr_layer
from anonymization_pipeline.pdf import PDFIUM_LOCK
from anonymization_pipeline.pdf.geometry import Geometry, Rect
from anonymization_pipeline.pdf.pages import is_scanned
from anonymization_pipeline.pipeline_config import PipelineConfig
from anonymization_pipeline.render import vault
from anonymization_pipeline.render.boxes import Box, from_outcome
from anonymization_pipeline.render.markdown import Rendered, render
from anonymization_pipeline.render.pdf import redact
from anonymization_pipeline.verify.pdf import Failure, verify

# progress(phase): called at the start of every phase; it may raise to cancel the run
Progress = Callable[[str], None]
PHASES = ("ingest", "detection", "redaction", "verification")
Status = Literal["done", "blocked"]


@dataclass
class Anonymized:
    """Outcome of one document: counters, never values."""

    status: Status
    reason: (
        str | None
    )  # blocked: ocr_unavailable | verification_failed (PDF or Markdown)
    pages: int
    kind: str  # pdf | image
    scanned_pages: list[int] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)  # distinct values per category
    boxes: list[Box] = field(
        default_factory=list
    )  # as applied: page, rectangle, category
    strategies: dict[int, str] = field(default_factory=dict)
    failures: list[Failure] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    pdf: bytes | None = field(default=None, repr=False)  # only when done
    markdown: str | None = field(default=None, repr=False)  # only when done and enabled
    encrypted_map: bytes | None = field(default=None, repr=False)  # pseudonym mode
    mode: str | None = None  # of the Markdown
    replacements: int = 0  # replacements in the Markdown


@dataclass
class _Page:
    layers: list[TextLayer]
    scanned: bool
    regions: list[Box]  # visual personal data


def _no_progress(phase: str) -> None:
    """Default progress callback."""


def _render(page: pdfium.PdfPage, dpi: int) -> np.ndarray:
    bitmap = page.render(scale=dpi / 72, draw_annots=False, may_draw_forms=False)
    try:
        return np.asarray(bitmap.to_pil().convert("RGB"))
    finally:
        bitmap.close()


def _image_bounds(page: pdfium.PdfPage, geo: Geometry) -> list[Rect]:
    out = []
    for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=8):
        left, bottom, right, top = obj.get_bounds()
        out.append(geo.to_display(left, bottom, right, top))
    return out


def _read_page(
    data: bytes, index: int, cfg: PipelineConfig, ocr: ocr_module.Engine | None
) -> _Page:
    """Text layers and visual regions of one page."""
    dpi = cfg.ingest.render_dpi
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(data)
        try:
            page = pdf[index]
            geo = Geometry(page)
            size = (geo.width, geo.height)
            layers = [native_layer(page, index)]
            scanned = is_scanned(page)
            image = _render(page, dpi) if cfg.detection.detectors.visual else None
            bounds = _image_bounds(page, geo)
            ocr_image = (
                _render(page, cfg.ocr.dpi) if scanned and ocr is not None else None
            )
            page.close()
        finally:
            pdf.close()
    regions: list[Box] = []
    scale = dpi / 72
    if image is not None:
        regions += visual.codes(index, image, scale)
    words: list[ocr_module.Word] = []
    if ocr_image is not None and ocr is not None:
        words = ocr_module.page_words(ocr, ocr_image, size[0], cfg.ocr)
        layers.append(ocr_layer(index, words))
    if image is not None:
        if scanned:
            regions += visual.stamp_from_text(index, words)
            regions += visual.unexplained_ink(index, image, scale, words)
        else:
            regions += visual.embedded_images(index, image, scale, size, bounds)
    return _Page(layers, scanned, regions)


def _page_count(data: bytes) -> int:
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(data)
        try:
            return len(pdf)
        finally:
            pdf.close()


def _markdown_texts(pages: list[_Page]) -> list[str]:
    """Text of every page for the Markdown: OCR on scans, otherwise the native text layer."""
    out = []
    for page in pages:
        by_source = {layer.source: layer for layer in page.layers}
        layer = by_source.get("ocr") if page.scanned else None
        out.append((layer or by_source["native"]).text)
    return out


def _map(source: bytes, rendered: Rendered, key: str) -> bytes:
    """The encrypted map of the pseudonym mode (it holds the original values)."""
    doc_sha = vault.sha256(source)
    occurrences = [
        {
            "start": o.start,
            "end": o.end,
            "replacement": o.replacement,
            "original": o.original,
        }
        for o in rendered.occurrences
    ]
    content = vault.map_content("documento", doc_sha, rendered.markdown, occurrences)
    return vault.encrypt_map(content, key, doc_sha)


def anonymize(
    data: bytes,
    cfg: PipelineConfig,
    progress: Progress = _no_progress,
    ner: Engine | None = None,
    ocr: ocr_module.Engine | None = None,
    key: str | None = None,
) -> Anonymized:
    """Anonymize one uploaded document.

    Args:
        data: Uploaded bytes (PDF or image).
        cfg: Configuration of the request.
        progress: Called at the start of every phase.
        ner: The NER model loaded at startup, required when NER is enabled.
        ocr: The OCR model loaded at startup, required when OCR is enabled.
        key: Key of the pseudonym map (pseudonym mode).

    Raises:
        DocumentError: Empty, unsupported, unreadable, encrypted or too large file.
        DetectorUnavailable: NER or OCR is enabled and its model was not loaded (never a silent
            drop in protection).
        VaultError: Pseudonym mode without a key.
    """
    if cfg.ocr.enabled and ocr is None:
        raise DetectorUnavailable("OCR enabled but no model loaded")
    if cfg.markdown.enabled and cfg.redaction.mode == "pseudonym" and not key:
        raise vault.VaultError("Modalità pseudonimo: serve una chiave per la mappa")
    timings: dict[str, float] = {}
    t0 = t = time.perf_counter()

    def mark(phase: str) -> None:
        nonlocal t
        now = time.perf_counter()
        timings[phase] = round(now - t, 3)
        t = now

    progress("ingest")
    source = ingest(data, cfg.ingest)
    pages = [
        _read_page(source.pdf, i, cfg, ocr if cfg.ocr.enabled else None)
        for i in range(_page_count(source.pdf))
    ]
    scanned = [i for i, p in enumerate(pages) if p.scanned]
    mark("ingest")
    result = Anonymized(
        "blocked", None, source.pages, source.kind, scanned, timings=timings
    )
    if scanned and not cfg.ocr.enabled:
        result.reason = "ocr_unavailable"
        timings["total"] = round(time.perf_counter() - t0, 3)
        return result

    progress("detection")
    layers = [layer for p in pages for layer in p.layers]
    outcome = detect(layers, cfg, ner)
    values = outcome.values(layers)
    active = set(cfg.active())
    regions = visual.dedupe(
        [b for p in pages for b in p.regions if b.category in active]
    )
    counts = Counter(c for _, c in values) + Counter(b.category for b in regions)
    result.counts = dict(sorted(counts.items()))
    mark("detection")

    progress("redaction")
    boxes = from_outcome(layers, outcome) + regions
    redacted = redact(source.pdf, boxes, cfg.redaction, set(scanned))
    result.boxes, result.strategies = redacted.boxes, redacted.strategies
    rendered, left = None, []
    if cfg.markdown.enabled:
        rendered, left = render(_markdown_texts(pages), values, cfg)
    mark("redaction")

    progress("verification")
    report = verify(source.pdf, redacted, values, cfg, ner)
    surgical = {p for p, s in redacted.strategies.items() if s == "surgical"}
    if not report.ok and surgical:
        # a surgical page the verification refused is flattened and everything checked again;
        # a failure that names no page sends every surgical page to flatten
        pages = {f.page for f in report.failures}
        retry = surgical if None in pages else surgical & pages
        if retry:
            redacted = redact(source.pdf, boxes, cfg.redaction, set(scanned), retry)
            result.boxes, result.strategies = redacted.boxes, redacted.strategies
            report = verify(source.pdf, redacted, values, cfg, ner)
    for leftover in left:  # the Markdown: no replaced value left in clear text
        report.fail("markdown_leftover", leftover.page - 1, leftover.category)
    mark("verification")
    timings["total"] = round(time.perf_counter() - t0, 3)
    result.failures = report.failures
    if not report.ok:
        result.reason = "verification_failed"
        return result
    result.status, result.pdf = "done", redacted.pdf
    if rendered is not None:
        result.markdown, result.mode = rendered.markdown, cfg.redaction.mode
        result.replacements = len(rendered.occurrences)
        if cfg.redaction.mode == "pseudonym" and key:
            result.encrypted_map = _map(source.pdf, rendered, key)
    return result
