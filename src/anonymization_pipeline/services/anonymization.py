"""Anonymization of one document: ingest -> text layers -> detection -> redaction -> verification.

CPU-bound and synchronous: the job worker runs it in a thread. A document is released only when
every check of the verification passed; otherwise it is blocked and no redacted file is kept.
A document with scanned pages is blocked before any redaction while no OCR engine is available:
its text would never be read, so nothing on those pages would be redacted.
"""

import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import pypdfium2 as pdfium

from anonymization_pipeline.detection import detect
from anonymization_pipeline.detectors.ner import Engine
from anonymization_pipeline.ingest import ingest
from anonymization_pipeline.layers import TextLayer
from anonymization_pipeline.layers.native import native_layer
from anonymization_pipeline.pdf import PDFIUM_LOCK
from anonymization_pipeline.pdf.pages import is_scanned
from anonymization_pipeline.pipeline_config import PipelineConfig
from anonymization_pipeline.render.boxes import Box, from_outcome
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
    reason: str | None  # blocked: ocr_unavailable | verification_failed
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


def _no_progress(phase: str) -> None:
    """Default progress callback."""


def _read(data: bytes) -> tuple[list[TextLayer], list[int]]:
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(data)
        try:
            layers: list[TextLayer] = []
            scanned: list[int] = []
            for i in range(len(pdf)):
                page = pdf[i]
                layers.append(native_layer(page, i))
                if is_scanned(page):
                    scanned.append(i)
                page.close()
            return layers, scanned
        finally:
            pdf.close()


def anonymize(
    data: bytes,
    cfg: PipelineConfig,
    progress: Progress = _no_progress,
    ner: Engine | None = None,
) -> Anonymized:
    """Anonymize one uploaded document.

    Args:
        data: Uploaded bytes (PDF or image).
        cfg: Configuration of the request.
        progress: Called at the start of every phase.
        ner: The NER model loaded at startup, required when NER is enabled.

    Raises:
        DocumentError: Empty, unsupported, unreadable, encrypted or too large file.
        DetectorUnavailable: NER is enabled and no model was loaded (never a silent drop).
    """
    timings: dict[str, float] = {}
    t0 = t = time.perf_counter()

    def mark(phase: str) -> None:
        nonlocal t
        now = time.perf_counter()
        timings[phase] = round(now - t, 3)
        t = now

    progress("ingest")
    source = ingest(data, cfg.ingest)
    layers, scanned = _read(source.pdf)
    mark("ingest")
    result = Anonymized(
        "blocked", None, source.pages, source.kind, scanned, timings=timings
    )
    if scanned:
        result.reason = "ocr_unavailable"
        timings["total"] = round(time.perf_counter() - t0, 3)
        return result

    progress("detection")
    outcome = detect(layers, cfg, ner)
    values = outcome.values(layers)
    result.counts = dict(sorted(Counter(c for _, c in values).items()))
    mark("detection")

    progress("redaction")
    redacted = redact(source.pdf, from_outcome(layers, outcome), cfg.redaction)
    result.boxes, result.strategies = redacted.boxes, redacted.strategies
    mark("redaction")

    progress("verification")
    report = verify(source.pdf, redacted, values, cfg, ner)
    mark("verification")
    timings["total"] = round(time.perf_counter() - t0, 3)
    result.failures = report.failures
    if report.ok:
        result.status, result.pdf = "done", redacted.pdf
    else:
        result.reason = "verification_failed"
    return result
